"""Small synthetic AOV Mesh bundles; no game assets are distributed."""
from types import SimpleNamespace
import struct
from AssetbundleUtils.UnityPy_AOV.files import ObjectReader, BundleFile
from AssetbundleUtils.UnityPy_AOV.files.File import File
from AssetbundleUtils.UnityPy_AOV.files.SerializedFile import SerializedFile, SerializedType
from AssetbundleUtils.UnityPy_AOV.streams import EndianBinaryReader
from AssetbundleUtils.UnityPy_AOV.enums import ClassIDType, BuildTarget
from AssetbundleUtils.UnityPy_AOV.helpers.TypeTreeHelper import get_nodes


def default_tree(nodes):
    node = nodes[0]
    if node.m_Type == 'string':
        return ''
    if node.m_Type == 'TypelessData':
        return b''
    if node.m_Type == 'bool':
        return False
    if node.m_Type in ('float', 'double'):
        return 0.0
    if len(nodes) > 1 and nodes[1].m_Type == 'Array':
        return []
    if len(nodes) == 1:
        return 0
    result = {}
    i = 1
    while i < len(nodes):
        children = get_nodes(nodes, i)
        result[nodes[i].m_Name] = default_tree(children)
        i += len(children)
    return result


def mesh_object(skin=True):
    assets = SerializedFile.__new__(SerializedFile)
    File.__init__(assets, name='CAB-aov-test')
    assets.header = SimpleNamespace(version=22, endian='<', reserved=b'\0'*3)
    assets.set_version('2022.3.5f1')
    assets._m_target_platform = int(BuildTarget.Android)
    assets.target_platform = BuildTarget.Android
    assets._enable_type_tree = False
    assets.big_id_enabled = False
    assets.script_types = []; assets.externals = []; assets.ref_types = []
    assets.userInformation = ''; assets.unknown = 0; assets._container = {}; assets._cache = {}
    assets.objects = {}; assets.container_ = {}
    typ = SerializedType.__new__(SerializedType)
    typ.class_id = 43; typ.is_stripped_type = False; typ.script_type_index = -1
    typ.old_type_hash = b'\0'*16; typ.nodes = []
    assets.types = [typ]
    obj = ObjectReader.__new__(ObjectReader)
    obj.assets_file = assets; obj.reader = EndianBinaryReader(b'',endian='<')
    obj.version = assets.version; obj.version2 = 22; obj.platform = BuildTarget.Android
    obj.build_type = assets.build_type; obj.serialized_type = typ
    obj.path_id = 101; obj.class_id = 43; obj.type = ClassIDType.Mesh; obj.type_id = 0
    obj.byte_start = 0; obj.byte_size = 0; obj.data = b''
    obj._object_read_depth = 0; obj._in_object_reader = False; obj._read_until = 0
    assets.objects[101] = obj
    tree = default_tree(obj.get_typetree_nodes())
    tree['m_Name'] = 'triangle'; tree['m_IsInUse'] = 1; tree['m_IsReadable'] = True
    tree['m_IndexBuffer'] = list(struct.pack('<3H',0,1,2))
    tree['m_SubMeshes'] = [dict(firstByte=0,indexCount=3,topology=0,baseVertex=0,
                              firstVertex=0,vertexCount=3,localAABB=dict(m_Center=dict(x=.5,y=.5,z=0.),m_Extent=dict(x=.5,y=.5,z=0.)))]
    tree['m_LocalAABB'] = tree['m_SubMeshes'][0]['localAABB']
    channels = [dict(stream=0,offset=0,format=0,dimension=0) for _ in range(14)]
    channels[0]['dimension']=3
    channels[1].update(offset=12,dimension=3)
    channels[4].update(offset=24,dimension=2)
    channels[5].update(offset=32,dimension=2)
    if skin:
        channels[12].update(offset=40,dimension=4)
        channels[13].update(offset=56,format=10,dimension=4)
        tree['m_BindPose'] = [{f'e{r}{c}': float(r==c) for r in range(4) for c in range(4)} for _ in range(3)]
        tree['m_BoneNameHashes'] = [10,11,12]
        tree['m_BonesAABB'] = [dict(m_Min=dict(x=0.,y=0.,z=0.),m_Max=dict(x=1.,y=1.,z=0.)) for _ in range(3)]
    raw = bytearray()
    for i, pos in enumerate([(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)]):
        raw.extend(struct.pack('<3f3f2f2f',*pos,0,0,1,i==1,i==2,10+i,20+i))
        if skin:
            raw.extend(struct.pack('<4f4I',1,0,0,0,i,0,0,0))
    tree['m_VertexData'].update(m_VertexCount=3,m_Channels=channels,m_DataSize=bytes(raw))
    obj.save_typetree(tree)
    return obj


def bundle_bytes(obj, packer='lz4', resources=None):
    bundle = BundleFile.__new__(BundleFile)
    File.__init__(bundle, name='test.assetbundle')
    bundle.HeaderAESKey = bytes.fromhex('e3056214d60a202536961b0774dc2402')
    bundle.HeaderAESIV = bytes.fromhex('1d6eeb4c86a94544457212212b43252f')
    bundle.signature = 'UnityFS'; bundle.version = 6
    bundle.version_player = '5.x.x'; bundle.version_engine = '2022.3.5f1'
    bundle._data_flags = 194; bundle._block_info_flags = 2
    bundle.files = {'CAB-aov-test':obj.assets_file}
    for name, data in (resources or {}).items():
        resource = EndianBinaryReader(data); resource.flags = 0
        bundle.files[name] = resource
    obj.assets_file.flags = 4; obj.assets_file.parent = bundle
    return bundle.save(packer)
