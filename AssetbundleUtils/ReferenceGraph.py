# -*- coding: utf-8 -*-
"""Dynamic cross-bundle PPtr relationship graph.

Every serialized object is registered as a node immediately.  Reference-heavy
types are expanded during project indexing, while any remaining type can be
expanded on demand.  Consumers therefore share one authoritative forward and
reverse graph instead of implementing unrelated name-based matching rules.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from threading import RLock
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


AssetKey = Tuple[int, int]


REFERENCE_SOURCE_TYPES = {
    "AssetBundle", "GameObject", "Transform", "RectTransform",
    "Animator", "Animation", "AnimatorController", "RuntimeAnimatorController",
    "MonoBehaviour", "Prefab", "PrefabInstance",
    "SkinnedMeshRenderer", "MeshRenderer", "MeshFilter",
    "ParticleSystem", "ParticleSystemRenderer", "TrailRenderer", "LineRenderer",
    "Material", "Sprite", "SpriteAtlas", "Avatar", "AnimationClip",
}


def iter_pptrs(value, field_path: str = ""):
    """Yield non-null Unity PPtrs from dictionaries, lists and tuple maps."""
    if isinstance(value, dict):
        if "m_FileID" in value and "m_PathID" in value:
            if int(value.get("m_PathID", 0)):
                yield field_path, value
            return
        for key, item in value.items():
            path = f"{field_path}/{key}" if field_path else str(key)
            yield from iter_pptrs(item, path)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from iter_pptrs(item, f"{field_path}[{index}]")


@dataclass(frozen=True)
class ReferenceNode:
    file_index: int
    path_id: int
    asset_type: str

    @property
    def key(self) -> AssetKey:
        return self.file_index, self.path_id

    @property
    def id(self) -> str:
        return f"f{self.file_index}:p{self.path_id}"


@dataclass(frozen=True)
class ReferenceEdge:
    source: AssetKey
    field: str
    target: AssetKey
    target_type: str


@dataclass(frozen=True)
class UnresolvedReference:
    source: AssetKey
    field: str
    file_id: int
    path_id: int


class CrossBundleReferenceGraph:
    """Thread-safe, incrementally expandable graph backed by one project index."""

    def __init__(self, project):
        self.project = project
        self.nodes: Dict[AssetKey, ReferenceNode] = {}
        self.forward: Dict[AssetKey, List[ReferenceEdge]] = defaultdict(list)
        self.reverse: Dict[AssetKey, List[ReferenceEdge]] = defaultdict(list)
        self.unresolved: Dict[AssetKey, List[UnresolvedReference]] = defaultdict(list)
        self.expanded = set()
        self._lock = RLock()
        for file_index, objects in enumerate(project.objects):
            for path_id, obj in objects.items():
                key = int(file_index), int(path_id)
                self.nodes[key] = ReferenceNode(
                    key[0], key[1], str(obj.type.name)
                )

    def expand(self, key: AssetKey) -> List[ReferenceEdge]:
        key = int(key[0]), int(key[1])
        with self._lock:
            if key in self.expanded:
                return list(self.forward.get(key, ()))
            self.expanded.add(key)
        obj = self.project.object(*key)
        if obj is None:
            return []
        try:
            tree = self.project.tree(*key)
        except Exception:
            return []
        edges = []
        unresolved = []
        seen = set()
        for field_path, pointer in iter_pptrs(tree):
            target_file_index, target = self.project.resolve_pptr(
                obj, key[0], pointer
            )
            if target is None or target_file_index is None:
                unresolved.append(UnresolvedReference(
                    key, field_path, int(pointer.get("m_FileID", 0)),
                    int(pointer.get("m_PathID", 0)),
                ))
                continue
            target_key = int(target_file_index), int(target.path_id)
            identity = field_path, target_key
            if identity in seen:
                continue
            seen.add(identity)
            edges.append(ReferenceEdge(
                key, field_path, target_key, str(target.type.name)
            ))
        with self._lock:
            self.forward[key] = edges
            self.unresolved[key] = unresolved
            for edge in edges:
                if edge not in self.reverse[edge.target]:
                    self.reverse[edge.target].append(edge)
        return list(edges)

    def expand_types(self, asset_types: Iterable[str] = REFERENCE_SOURCE_TYPES):
        selected = {str(value) for value in asset_types}
        for key, node in self.nodes.items():
            if node.asset_type in selected:
                self.expand(key)

    def expand_all(self):
        for key in self.nodes:
            self.expand(key)

    def outgoing(self, key: AssetKey, expand: bool = True) -> List[ReferenceEdge]:
        if expand:
            self.expand(key)
        return list(self.forward.get((int(key[0]), int(key[1])), ()))

    def incoming(self, key: AssetKey) -> List[ReferenceEdge]:
        return list(self.reverse.get((int(key[0]), int(key[1])), ()))

    def walk(
        self, starts: Sequence[AssetKey], direction: str = "outgoing",
        max_depth: Optional[int] = None, target_types: Optional[Iterable[str]] = None,
    ) -> List[ReferenceNode]:
        wanted = {str(value) for value in target_types} if target_types else None
        queue = deque(((int(key[0]), int(key[1])), 0) for key in starts)
        visited = set()
        result = []
        while queue:
            key, depth = queue.popleft()
            if key in visited:
                continue
            visited.add(key)
            node = self.nodes.get(key)
            if node is not None and (wanted is None or node.asset_type in wanted):
                result.append(node)
            if max_depth is not None and depth >= max_depth:
                continue
            edges = (
                self.outgoing(key, expand=True)
                if direction == "outgoing" else self.incoming(key)
            )
            for edge in edges:
                queue.append((
                    edge.target if direction == "outgoing" else edge.source,
                    depth + 1,
                ))
        return result

    def invalidate(self, key: AssetKey):
        """Drop cached edges after an in-memory serialized object replacement."""
        key = int(key[0]), int(key[1])
        with self._lock:
            old = self.forward.pop(key, [])
            self.unresolved.pop(key, None)
            self.expanded.discard(key)
            for edge in old:
                incoming = self.reverse.get(edge.target, [])
                self.reverse[edge.target] = [item for item in incoming if item != edge]

    def refresh(self, keys: Iterable[AssetKey]) -> int:
        """Re-read changed objects and atomically restore their graph edges.

        Asset replacement keeps the original PathID, so graph nodes stay stable;
        only the changed objects' outgoing PPtrs need to be invalidated and read
        again. Reverse edges are updated by :meth:`invalidate`/:meth:`expand`.
        """
        normalized = list(dict.fromkeys(
            (int(key[0]), int(key[1])) for key in keys
        ))
        for key in normalized:
            self.invalidate(key)
        for key in normalized:
            self.expand(key)
        return len(normalized)

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self.forward.values())
