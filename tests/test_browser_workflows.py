"""Exercise actual browser import/save callbacks with UI-only dialog stubs."""
import queue
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
import AssetbundleUtils.AssetsList as ui
from AssetbundleUtils.AssetsList import AssetBrowser
from AssetbundleUtils import UnityPy_AOV
from AssetbundleUtils.TypeTreeImport import export_typetree_dump
from AssetbundleUtils.PreviewWorker import apply_project_overrides
from tests.fixtures import mesh_object,bundle_bytes


class Control:
    def set_enabled(self,value): pass
    def pack(self,**kwargs): pass
    def pack_forget(self): pass
    def start(self,*args): pass
    def stop(self): pass


class Browser(AssetBrowser):
    def __init__(self,source):
        self.paths=[str(source)];self.env_list=[UnityPy_AOV.load(str(source))]
        self.target=next(iter(self.env_list[0].objects));self.window=None
        self.loading=False;self.operation_busy=False;self.modified={};self.edit_revision=0
        self.animation_project=None;self.effect_project=None;self.animation_model=None
        self.input_path=str(source);self.is_directory=False
        self.save_button=Control();self.fingerprint_save_button=Control();self.progress=Control()
        self.executor=ThreadPoolExecutor(max_workers=1);self.events=queue.Queue();self.previews=[]
    def _selected_records(self): return [(0,self.target)]
    def _update_button_states(self): pass
    def _set_status(self,*args,**kwargs): pass
    def _schedule_virtual_render(self): pass
    def _request_preview(self,index,obj): self.previews.append(obj.path_id)
    def finish(self):
        event=self.events.get(timeout=10)
        if event[0]=='operation_done':self._operation_done(*event[1:])
        elif event[0]=='operation_error':self._operation_error(*event[1:]);raise AssertionError(event)
        elif event[0]=='save_done':self._save_done(*event[1:]);return event
        else:raise AssertionError(event)


@pytest.fixture
def browser(tmp_path,monkeypatch):
    source=tmp_path/'input.assetbundle';source.write_bytes(bundle_bytes(mesh_object()))
    monkeypatch.setattr(ui,'show_dialog',lambda *args,**kwargs: True)
    b=Browser(source)
    yield b
    b.executor.shutdown()


def test_ui_mesh_import_dirty_preview_and_save(browser,tmp_path,monkeypatch):
    obj_path=tmp_path/'mesh.obj';obj_path.write_text(browser.target.read(False).export())
    monkeypatch.setattr(ui,'askopenfile',lambda *args,**kwargs: str(obj_path))
    browser.import_mesh();browser.finish()
    assert browser.modified=={(0,101):'Mesh'}
    assert browser.previews==[101]
    output=tmp_path/'out';output.mkdir()
    monkeypatch.setattr(ui,'askdirectory',lambda *args,**kwargs:str(output))
    browser.save_bundles();event=browser.finish()
    assert event[2] is None
    reloaded=next(iter(UnityPy_AOV.load(str(output/'input.assetbundle')).objects))
    assert reloaded.get_raw_data()==browser.target.get_raw_data()
    assert not browser.operation_busy


def test_ui_dump_import_dirty_then_save(browser,tmp_path,monkeypatch):
    path=tmp_path/'dump.json';export_typetree_dump(browser.target,path)
    monkeypatch.setattr(ui,'askopenfile',lambda *args,**kwargs:str(path))
    browser.import_dump();browser.finish()
    assert browser.modified=={(0,101):'Dump'}
    assert browser.previews==[101]


def test_failed_save_does_not_overwrite_existing_file(browser,tmp_path,monkeypatch):
    output=tmp_path/'out';output.mkdir();target=output/'input.assetbundle';target.write_bytes(b'keep me')
    monkeypatch.setattr(ui,'askdirectory',lambda *args,**kwargs:str(output))
    monkeypatch.setattr(browser.env_list[0].file,'save',lambda *args:b'broken')
    browser.save_bundles();event=browser.finish()
    assert event[2] is not None
    assert target.read_bytes()==b'keep me'
    assert not browser.operation_busy


def test_preview_disabled_does_not_schedule_rendering():
    b=AssetBrowser.__new__(AssetBrowser);b.preview_generation=0;b.preview_after_job=None
    b.preview_enabled=False;b.calls=[]
    b._reset_preview=lambda *args:b.calls.append('reset')
    b._request_dump=lambda *args:b.calls.append('dump')
    b._resolve_effect_for_asset=lambda *args:pytest.fail('rendering should not be scheduled')
    b._request_preview(0,SimpleNamespace())
    assert b.calls==['reset','dump','reset']


def test_project_preview_overrides_refresh_mesh_and_animation_together():
    mesh=SimpleNamespace(set_raw_data=lambda raw: changes.append(('mesh',raw)))
    clip=SimpleNamespace(set_raw_data=lambda raw: changes.append(('clip',raw)))
    changes=[]
    project=SimpleNamespace(object=lambda i,p:mesh if p==101 else clip,
                            refresh_serialized_objects=lambda keys:changes.append(('refresh',keys)))
    apply_project_overrides(project,[(0,101,b'mesh edit'),(1,202,b'clip edit')])
    assert changes==[('mesh',b'mesh edit'),('clip',b'clip edit'),('refresh',[(0,101),(1,202)])]


def test_save_is_blocked_during_pending_import(browser,monkeypatch):
    browser.operation_busy=True
    monkeypatch.setattr(ui,'askdirectory',lambda *args,**kwargs:pytest.fail('Save must not open while import is running'))
    browser.save_bundles()


def test_invalid_raw_import_rolls_back(browser,tmp_path,monkeypatch):
    path=tmp_path/'bad.dat';path.write_bytes(b'invalid mesh')
    original=browser.target.get_raw_data()
    monkeypatch.setattr(ui,'askopenfile',lambda *args,**kwargs:str(path))
    browser.import_raw()
    event=browser.events.get(timeout=10)
    assert event[0]=='operation_error'
    assert browser.target.get_raw_data()==original
    assert not browser.modified


def test_project_export_includes_unsaved_ui_edits(browser,tmp_path,monkeypatch):
    from pathlib import Path
    tree=browser.target.read_typetree();tree['m_Name']='unsaved_name';browser.target.save_typetree(tree)
    browser.modified[(0,101)]='Dump'
    output=tmp_path/'projects';output.mkdir()
    monkeypatch.setattr(ui,'askdirectory',lambda *args,**kwargs:str(output))
    browser.export_bundle_projects();browser.finish()
    exported=next(output.rglob('*.raw'))
    assert exported.read_bytes()==browser.target.get_raw_data()
    backup=next(output.rglob('*.assetbundle'))
    assert next(iter(UnityPy_AOV.load(str(backup)).objects)).read(False).m_Name=='unsaved_name'


def test_dump_transform_refresh_updates_materialized_model_position(tmp_path):
    from AssetbundleUtils.AnimationPipeline import AnimationProjectIndex
    assets=SimpleNamespace(name="CAB-model-test",externals=[])
    state={"x":0.0,"y":0.0,"z":0.0}
    game=SimpleNamespace(path_id=1,type=SimpleNamespace(name="GameObject"),assets_file=assets,
                         read=lambda:SimpleNamespace(name="root",m_Components=[]),
                         read_typetree=lambda:{"m_Name":"root","m_Component":[]},
                         peek_name=lambda *args:"root")
    transform=SimpleNamespace(path_id=2,type=SimpleNamespace(name="Transform"),assets_file=assets,
        read=lambda:SimpleNamespace(m_GameObject=SimpleNamespace(path_id=1),m_Father=SimpleNamespace(path_id=0),
                                     m_Children=[],m_LocalPosition=SimpleNamespace(**state),
                                     m_LocalRotation=SimpleNamespace(x=0.,y=0.,z=0.,w=1.),
                                     m_LocalScale=SimpleNamespace(x=1.,y=1.,z=1.)),
        read_typetree=lambda:{"m_LocalPosition":dict(state)},peek_name=lambda *args:"transform")
    project=AnimationProjectIndex([str(tmp_path/'model.assetbundle')],[SimpleNamespace(objects=[game,transform])])
    assert project.transforms[(0,2)].position==(0.,0.,0.)
    state["x"]=7.
    project.refresh_serialized_objects([(0,2)])
    assert project.transforms[(0,2)].position==(7.,0.,0.)


def test_dump_rename_refreshes_visible_name_size_and_search(browser):
    from AssetbundleUtils.AssetsList import AssetRow
    obj=browser.target
    row=AssetRow(0,'input.assetbundle','triangle','Mesh',101,obj.byte_size,obj,'triangle mesh 101')
    browser.row_lookup={row.key:row};browser.all_rows=[row]
    browser._queue_view_rebuild=lambda **kwargs:None
    tree=obj.read_typetree();tree['m_Name']='renamed_mesh';obj.save_typetree(tree)
    browser._mark_modified(0,obj,'Dump')
    updated=browser.row_lookup[row.key]
    assert updated.name=='renamed_mesh'
    assert updated.byte_size==obj.byte_size
    assert 'renamed_mesh' in updated.search_text
