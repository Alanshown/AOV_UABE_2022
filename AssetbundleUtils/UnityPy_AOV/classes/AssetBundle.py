from .NamedObject import NamedObject
from .PPtr import PPtr
from ..streams import EndianBinaryWriter


class AssetInfo:
    def __init__(self, reader):
        self.preload_index = reader.read_int()
        self.preload_size = reader.read_int()
        self.asset = PPtr(reader)

    def save(self, writer):
        writer.write_int(self.preload_index)
        writer.write_int(self.preload_size)
        self.asset.save(writer)


class AssetBundle(NamedObject):
    def __init__(self, reader):
        super().__init__(reader=reader)
        version = self.version
        preload_table_size = reader.read_int()
        self.m_PreloadTable = [PPtr(reader) for _ in range(preload_table_size)]
        container_size = reader.read_int()
        self.m_Container = {}
        self.m_ContainerEntries = []
        # TODO - m_Container is a multi-dict, multiple values can have the same key
        for i in range(container_size):
            key = reader.read_aligned_string()
            value = AssetInfo(reader)
            self.m_ContainerEntries.append((key, value))
            self.m_Container[key] = value
        self.m_MainAsset = AssetInfo(reader)

        self.m_ScriptCompatibility = []
        if version == (5, 4):
            compatibility_size = reader.read_int()
            self.m_ScriptCompatibility = [
                (reader.read_int(), reader.read_int())
                for _ in range(compatibility_size)
            ]

        compatibility_size = reader.read_int()
        self.m_ClassCompatibility = []
        for _ in range(compatibility_size):
            class_id = reader.read_int()
            self.m_ClassCompatibility.append((class_id, AssetInfo(reader)))

        self.m_RuntimeCompatibility = (
            reader.read_u_int() if version >= (4, 2) else 0
        )
        if version >= (5,):
            self.m_AssetBundleName = reader.read_aligned_string()
            self.m_Dependencies = reader.read_string_array()
            self.m_IsStreamedSceneAssetBundle = reader.read_boolean()
        else:
            self.m_AssetBundleName = ""
            self.m_Dependencies = []
            self.m_IsStreamedSceneAssetBundle = False
        self._trailing_data = bytes(
            reader.read_bytes(max(0, reader.Length - reader.Position))
        )

    def save(self, writer: EndianBinaryWriter = None):
        if writer is None:
            writer = EndianBinaryWriter(endian=self.reader.endian)
        super().save(writer)
        writer.write_int(len(self.m_PreloadTable))
        for pointer in self.m_PreloadTable:
            pointer.save(writer)

        entries = getattr(self, "m_ContainerEntries", None)
        if entries is None:
            entries = list(self.m_Container.items())
        writer.write_int(len(entries))
        for key, value in entries:
            writer.write_aligned_string(key)
            value.save(writer)
        self.m_MainAsset.save(writer)

        if self.version == (5, 4):
            writer.write_int(len(self.m_ScriptCompatibility))
            for first, second in self.m_ScriptCompatibility:
                writer.write_int(first)
                writer.write_int(second)

        writer.write_int(len(self.m_ClassCompatibility))
        for class_id, value in self.m_ClassCompatibility:
            writer.write_int(class_id)
            value.save(writer)

        if self.version >= (4, 2):
            writer.write_u_int(self.m_RuntimeCompatibility)
        if self.version >= (5,):
            writer.write_aligned_string(self.m_AssetBundleName)
            writer.write_string_array(self.m_Dependencies)
            writer.write_boolean(self.m_IsStreamedSceneAssetBundle)
        writer.write_bytes(self._trailing_data)
        self.set_raw_data(writer.bytes)
