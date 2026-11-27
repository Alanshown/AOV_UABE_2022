from AssetbundleUtils import BundleProject as P, UnityPy_AOV as U
from AssetbundleUtils.UnityPy_AOV.classes.AssetBundle import AssetBundle, AssetInfo
from AssetbundleUtils.UnityPy_AOV.enums import ClassIDType
from tests.fixtures import bundle_bytes, mesh_object


def _fixture():
    mesh = mesh_object()
    af = mesh.assets_file
    # An external pointer with the same PathID as a deleted local asset must
    # remain in the preload table and both catalog slots.
    other = P._shallow_clone(mesh)
    other.path_id = 102
    af.objects[102] = other
    catalog = P._shallow_clone(mesh)
    ty = P._shallow_clone(mesh.serialized_type)
    ty.class_id = int(ClassIDType.AssetBundle)
    af.types.append(ty)
    catalog.serialized_type = ty
    catalog.type = ClassIDType.AssetBundle
    catalog.class_id = ty.class_id
    catalog.type_id = 1
    catalog.path_id = 1
    af.objects[1] = catalog

    def ptr(path, file=0):
        return P._make_pointer(af, path, file_id=file)

    def info(path, file=0, *, index=0, size=3):
        item = AssetInfo.__new__(AssetInfo)
        item.asset = ptr(path, file)
        item.preload_index = index
        item.preload_size = size
        return item

    base = AssetBundle.__new__(AssetBundle)
    base.__dict__.update(
        reader=catalog, platform=catalog.platform, version=af.version,
        m_Name="catalog",
        m_PreloadTable=[ptr(101), ptr(101, 1), ptr(102)],
        m_ContainerEntries=[("gone", info(101)), ("triangle", info(101, 1)), ("keep", info(102, index=2, size=1))],
        m_MainAsset=info(101),
        m_ClassCompatibility=[(22, info(101)), (33, info(101, 1)), (44, info(102, index=2, size=1))],
        m_RuntimeCompatibility=0, m_AssetBundleName="catalog",
        m_Dependencies=[], m_IsStreamedSceneAssetBundle=False, _trailing_data=b"",
        m_ObjectHideFlags=0,
    )
    base.save()
    return U.load(bundle_bytes(mesh))


def test_catalog_removes_only_deleted_local_entries_and_rebases_preloads():
    env = _fixture()
    reader = next(obj for obj in env.objects if obj.path_id == 1)
    del reader.assets_file.objects[101]
    P._sync_assetbundle_catalogs(
        env, {101}, [], [{"path_id": 101, "from": "triangle", "to": "renamed"}]
    )
    refreshed = U.load(env.file.save("lz4"))
    bundle = next(obj for obj in refreshed.objects if obj.path_id == 1).read(False)
    assert [(p.file_id, p.path_id) for p in bundle.m_PreloadTable] == [(1, 101), (0, 102)]
    assert [key for key, _ in bundle.m_ContainerEntries] == ["triangle", "keep"]
    assert [(key, v.asset.file_id, v.asset.path_id) for key, v in bundle.m_ClassCompatibility] == [
        (33, 1, 101), (44, 0, 102)
    ]
    for entries in (bundle.m_ContainerEntries, bundle.m_ClassCompatibility):
        assert [(v.preload_index, v.preload_size) for _, v in entries] == [(0, 2), (1, 1)]
    assert (bundle.m_MainAsset.asset.file_id, bundle.m_MainAsset.asset.path_id) == (0, 0)
    assert (bundle.m_MainAsset.preload_index, bundle.m_MainAsset.preload_size) == (0, 0)
