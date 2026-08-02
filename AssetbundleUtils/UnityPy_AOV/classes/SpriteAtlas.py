from .NamedObject import NamedObject
from .PPtr import PPtr
from .Sprite import SpriteSettings, SecondarySpriteTexture
from ..streams import EndianBinaryWriter


class SpriteAtlas(NamedObject):
    def __init__(self, reader):
        super().__init__(reader=reader)
        packed_sprites_size = reader.read_int()
        self.m_PackedSprites = [PPtr(reader) for _ in range(packed_sprites_size)]

        self.m_PackedSpriteNamesToIndex = reader.read_string_array()
        m_render_data_map_size = reader.read_int()
        self.m_RenderDataMap = {}
        for _ in range(m_render_data_map_size):
            first = bytes(reader.read_bytes(16))  # GUID keys must be hashable
            second = reader.read_long()
            value = SpriteAtlasData(reader)
            self.m_RenderDataMap[(first, second)] = value
        self.m_Tag = reader.read_aligned_string()
        self.m_IsVariant = reader.read_boolean()
        reader.align_stream()

    def save(self, writer: EndianBinaryWriter = None):
        if writer is None:
            writer = EndianBinaryWriter(endian=self.reader.endian)
        super().save(writer)
        writer.write_int(len(self.m_PackedSprites))
        for pointer in self.m_PackedSprites:
            pointer.save(writer)
        writer.write_string_array(self.m_PackedSpriteNamesToIndex)
        writer.write_int(len(self.m_RenderDataMap))
        for (guid, local_id), value in self.m_RenderDataMap.items():
            writer.write_bytes(guid)
            writer.write_long(local_id)
            value.save(writer)
        writer.write_aligned_string(self.m_Tag)
        writer.write_boolean(self.m_IsVariant)
        writer.align_stream()
        self.set_raw_data(writer.bytes)


class SpriteAtlasData:
    def __init__(self, reader):
        self.version = version = reader.version
        self.texture = PPtr(reader)  # Texture2D
        self.alphaTexture = PPtr(reader)  # Texture2D
        self.textureRect = reader.read_rectangle_f()
        self.textureRectOffset = reader.read_vector2()
        if version >= (2017, 2):  # 2017.2 and up
            self.atlasRectOffset = reader.read_vector2()
        self.uvTransform = reader.read_vector4()
        self.downscaleMultiplier = reader.read_float()
        self.settingsRaw = SpriteSettings(reader)

        if version >= (2020, 2):
            secondaryTexturesSize = reader.read_int()
            self.secondaryTextures = [
                SecondarySpriteTexture(reader) for _ in range(secondaryTexturesSize)
            ]
            reader.align_stream()

    def save(self, writer):
        self.texture.save(writer)
        self.alphaTexture.save(writer)
        writer.write_rectangle_f(self.textureRect)
        writer.write_vector2(self.textureRectOffset)
        if self.version >= (2017, 2):
            writer.write_vector2(self.atlasRectOffset)
        writer.write_vector4(self.uvTransform)
        writer.write_float(self.downscaleMultiplier)
        self.settingsRaw.save(writer)

        if self.version >= (2020, 2):
            writer.write_int(len(self.secondaryTextures))
            for texture in self.secondaryTextures:
                texture.save(writer)
            writer.align_stream()
