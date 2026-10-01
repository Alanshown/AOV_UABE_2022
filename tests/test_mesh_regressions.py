import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from AssetbundleUtils import UnityPy_AOV
from AssetbundleUtils.MeshImport import parse_obj, replace_mesh_from_obj, validate_mesh_payload
from AssetbundleUtils.BundleProject import export_bundle_project, rebuild_bundle_project
from AssetbundleUtils.TypeTreeImport import export_typetree_dump, import_typetree_dump, _encode, _decode
from tests.fixtures import mesh_object, bundle_bytes


def export_obj(obj,path):
    path.write_text(obj.read(False).export())
    return path


def test_aov_mesh_fallback_nodes_include_is_in_use():
    obj=mesh_object()
    assert obj.read_typetree()['m_IsInUse']==1
    assert obj.read(False).m_VertexCount==3
    assert obj.read_typetree()['m_Name']=='triangle'


def test_unedited_obj_keeps_skin_uv1_and_vertex_order(tmp_path):
    obj=mesh_object()
    before=obj.read(False)
    path=export_obj(obj,tmp_path/'mesh.obj')
    result=replace_mesh_from_obj(obj,str(path))
    after=obj.read(False)
    assert result.preserved_vertex_streams
    assert after.m_Vertices==before.m_Vertices
    assert after.m_Indices==before.m_Indices
    assert [s.boneIndex for s in after.m_Skin]==[s.boneIndex for s in before.m_Skin]
    assert after.m_UV1==before.m_UV1
    assert obj.read_typetree()['m_IsInUse']==1


def test_reordered_obj_remaps_all_channels(tmp_path):
    obj=mesh_object()
    path=tmp_path/'reordered.obj'
    path.write_text('v 0 1 0\nv -1 0 0\nv 0 0 0\nf 1 2 3\n')
    replace_mesh_from_obj(obj,str(path))
    mesh=obj.read(False)
    assert [s.boneIndex[0] for s in mesh.m_Skin]==[2,1,0]
    assert list(mesh.m_UV1)==[12,22,11,21,10,20]
    assert list(mesh.m_UV0)==[0,1,1,0,0,0]


def test_topology_change_preserves_extended_stream_and_skin(tmp_path):
    obj=mesh_object()
    path=tmp_path/'edited.obj'
    path.write_text('v 0 0 0\nv -1 0 0\nv 0 1 0\nv -1 1 0\nf 3 2 1\nf 3 4 2\n')
    replace_mesh_from_obj(obj,str(path))
    mesh=validate_mesh_payload(obj)
    assert mesh.m_VertexCount==4
    assert len(mesh.m_Skin)==4
    assert len(mesh.m_UV1)==8
    assert obj.read_typetree()['m_BonesAABB']


def test_groups_with_shared_mtl_stay_separate(tmp_path):
    path=tmp_path/'groups.obj'
    path.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\ng one\nusemtl shared\nf 1 2 3\ng two\nusemtl shared\nf 2 4 3\n')
    assert len(parse_obj(str(path)).submeshes)==2


@pytest.mark.parametrize('value',['nan','inf','-inf'])
def test_nonfinite_obj_rejected_without_mutation(tmp_path,value):
    obj=mesh_object(); original=obj.get_raw_data()
    path=tmp_path/'bad.obj'; path.write_text(f'v {value} 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
    with pytest.raises(ValueError): replace_mesh_from_obj(obj,str(path))
    assert obj.get_raw_data()==original


@pytest.mark.parametrize('packer',['original','lz4','aov-fingerprint-1','aov-fingerprint-2','aov-fingerprint-3'])
def test_real_bundle_save_reload_keeps_imported_mesh(tmp_path,packer):
    obj=mesh_object(); path=export_obj(obj,tmp_path/'mesh.obj')
    replace_mesh_from_obj(obj,str(path)); expected=obj.get_raw_data()
    loaded=UnityPy_AOV.load(bundle_bytes(obj,packer))
    target=next(o for o in loaded.objects if o.path_id==101)
    assert target.get_raw_data()==expected
    validate_mesh_payload(target)
    assert target.read(False).m_Skin[2].boneIndex[0]==2


def test_dump_json_roundtrip_and_edit(tmp_path):
    obj=mesh_object(); path=tmp_path/'dump.json'
    export_typetree_dump(obj,path)
    document=json.loads(path.read_text()); document['tree']['m_Name']='changed'
    path.write_text(json.dumps(document))
    import_typetree_dump(obj,path)
    assert obj.read(False).m_Name=='changed'
    assert obj.read_typetree()['m_IsInUse']==1
    loaded=UnityPy_AOV.load(bundle_bytes(obj))
    assert next(iter(loaded.objects)).read(False).m_Name=='changed'


def test_dump_bytes_and_map_tuples_are_lossless():
    tree={'blob':b'\0\xff','pairs':[('name',{'data':b'abc'})]}
    assert _decode(json.loads(json.dumps(_encode(tree))))==tree


@pytest.mark.parametrize('change',['path_id','type','schema','fields'])
def test_dump_rejects_wrong_target_or_incomplete_data(tmp_path,change):
    obj=mesh_object(); raw=obj.get_raw_data(); path=tmp_path/'dump.json'
    export_typetree_dump(obj,path); document=json.loads(path.read_text())
    if change=='schema': document['$schema']='display dump'
    elif change=='fields': del document['tree']['m_Name']
    elif change=='path_id': document['target']['path_id']=999
    else: document['target']['type']='Texture2D'
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError): import_typetree_dump(obj,path)
    assert obj.get_raw_data()==raw


def test_project_obj_edit_is_applied_on_rebuild(tmp_path):
    obj=mesh_object(skin=False); source=tmp_path/'input.assetbundle'
    source.write_bytes(bundle_bytes(obj))
    info=export_bundle_project(str(source),str(tmp_path/'export'))
    project=info['path']
    from pathlib import Path
    mesh_path=next(Path(project).rglob('*.obj'))
    mesh_path.write_text(mesh_path.read_text().replace('v -1 0 0','v -2 0 0'))
    output=tmp_path/'output.assetbundle'
    result=rebuild_bundle_project(project,str(output))
    assert result['edited_meshes']==1
    target=next(o for o in UnityPy_AOV.load(str(output)).objects if o.type.name=='Mesh')
    assert max(target.read(False).m_Vertices)==2


def test_translated_obj_keeps_vertex_skin_correspondence(tmp_path):
    obj=mesh_object();path=tmp_path/'translated.obj'
    path.write_text('v -10 0 0\nv -11 0 0\nv -10 1 0\nf 3 2 1\n')
    replace_mesh_from_obj(obj,str(path));mesh=obj.read(False)
    assert [s.boneIndex[0] for s in mesh.m_Skin]==[0,1,2]
    assert obj.read_typetree()['m_BonesAABB'][0]['m_Min']['x']==10


def test_export_obj_without_uv_or_normals_roundtrips(tmp_path):
    obj=mesh_object(skin=False);mesh=obj.read(False)
    mesh.m_UV0=[];mesh.m_Normals=[]
    path=tmp_path/'bare.obj';path.write_text(mesh.export())
    parsed=parse_obj(str(path))
    assert not parsed.has_uvs and not parsed.has_normals
    assert parsed.indices==[0,1,2]


def test_variable_bone_topology_edit_fails_and_restores_raw(tmp_path):
    obj=mesh_object();tree=obj.read_typetree();tree['m_VariableBoneCountWeights']['m_Data']=[1,2,3]
    obj.save_typetree(tree);raw=obj.get_raw_data()
    path=tmp_path/'changed.obj';path.write_text('v 0 0 0\nv -1 0 0\nv 0 1 0\nv -1 1 0\nf 3 2 1\nf 3 4 2\n')
    with pytest.raises(ValueError,match='Variable bone-count'):replace_mesh_from_obj(obj,str(path))
    assert obj.get_raw_data()==raw


def test_serialization_validation_failure_restores_raw(tmp_path,monkeypatch):
    obj=mesh_object();raw=obj.get_raw_data();path=export_obj(obj,tmp_path/'mesh.obj')
    import AssetbundleUtils.MeshImport as module
    monkeypatch.setattr(module,'validate_mesh_payload',lambda _: (_ for _ in ()).throw(ValueError('invalid skin')))
    with pytest.raises(ValueError,match='invalid skin'):replace_mesh_from_obj(obj,str(path))
    assert obj.get_raw_data()==raw


def test_renderer_bounds_expand_without_replacing_materials(tmp_path):
    from AssetbundleUtils.MeshImport import synchronize_mesh_renderer_bounds
    obj=mesh_object();tree=obj.read_typetree();tree['m_LocalAABB']['m_Center']['x']=10
    obj.save_typetree(tree)
    bounds={'m_Center':dict(x=0.,y=0.,z=0.),'m_Extent':dict(x=1.,y=1.,z=1.)}
    renderer_tree={'m_Mesh':{'m_FileID':0,'m_PathID':101},
                   'm_Materials':[{'m_FileID':1,'m_PathID':999}],
                   'm_AABB':deepcopy(bounds),'m_LocalAABB':deepcopy(bounds)}
    renderer=SimpleNamespace(type=SimpleNamespace(name='SkinnedMeshRenderer'),assets_file=obj.assets_file,
                             read_typetree=lambda:deepcopy(renderer_tree),get_raw_data=lambda:b'original',
                             save_typetree=lambda value:renderer_tree.update(value))
    changed=synchronize_mesh_renderer_bounds([SimpleNamespace(objects=[renderer])],obj)
    assert len(changed)==1
    assert renderer_tree['m_Materials']==[{'m_FileID':1,'m_PathID':999}]
    for field in ('m_AABB','m_LocalAABB'):
        assert renderer_tree[field]['m_Center']['x']+renderer_tree[field]['m_Extent']['x']>=10.5


def test_animation_rejects_nontransform_curves_before_mutating():
    from AssetbundleUtils.AnimationPipeline import replace_animation_from_fbx
    obj=SimpleNamespace(type=SimpleNamespace(name='AnimationClip'),path_id=202,get_raw_data=lambda:b'unchanged')
    tree={'m_ClipBindingConstant':{'genericBindings':[{'typeID':23,'attribute':1}]}}
    project=SimpleNamespace(object=lambda *args:obj,tree=lambda *args:tree)
    with pytest.raises(ValueError,match='transform-only'):
        replace_animation_from_fbx(project,0,202,None,'nonexistent.fbx')


def test_streamed_mesh_import_embeds_data_and_preserves_skin(tmp_path):
    obj=mesh_object();tree=obj.read_typetree();payload=bytes(tree['m_VertexData']['m_DataSize'])
    tree['m_VertexData']['m_DataSize']=b''
    tree['m_StreamData'].update(path='CAB-test.resS',offset=0,size=len(payload))
    obj.save_typetree(tree)
    loaded=UnityPy_AOV.load(bundle_bytes(obj,resources={'CAB-test.resS':payload}))
    target=next(iter(loaded.objects));path=export_obj(target,tmp_path/'mesh.obj')
    replace_mesh_from_obj(target,str(path))
    assert target.read_typetree()['m_StreamData']==dict(offset=0,size=0,path='')
    assert target.read(False).m_Skin[2].boneIndex[0]==2
    reloaded=UnityPy_AOV.load(loaded.file.save('lz4'))
    assert next(iter(reloaded.objects)).get_raw_data()==target.get_raw_data()


def test_aov_fallback_does_not_modify_global_tpk_nodes():
    from AssetbundleUtils.UnityPy_AOV.helpers import Tpk
    original=Tpk.get_typetree_nodes(43,(2022,3,5,0))
    mesh_object().read_typetree()
    assert not any(node.m_Name=='m_IsInUse' for node in original)


def test_noop_project_rebuild_keeps_raw_mesh_bytes(tmp_path):
    obj=mesh_object();source=tmp_path/'input.assetbundle';source.write_bytes(bundle_bytes(obj))
    info=export_bundle_project(str(source),str(tmp_path/'export'))
    output=tmp_path/'out.assetbundle';result=rebuild_bundle_project(info['path'],str(output))
    assert result['edited_meshes']==0
    target=next(iter(UnityPy_AOV.load(str(output)).objects))
    assert target.get_raw_data()==obj.get_raw_data()


def test_dump_rejects_new_dangling_pointers():
    from AssetbundleUtils.TypeTreeImport import validate_changed_references
    obj=mesh_object()
    with pytest.raises(ValueError,match='missing local'):
        validate_changed_references(obj,{},dict(m_Mesh=dict(m_FileID=0,m_PathID=999)))
    with pytest.raises(ValueError,match='external FileID'):
        validate_changed_references(obj,{},dict(m_Mesh=dict(m_FileID=1,m_PathID=101)))
    validate_changed_references(obj,{},dict(m_Mesh=dict(m_FileID=0,m_PathID=101)))
