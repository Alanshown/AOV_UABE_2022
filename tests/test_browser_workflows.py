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
        self.texture_preview_overrides={};self.sprite_preview_overrides={}
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


@pytest.mark.parametrize("replacement_format", ["raw", "dump"])
@pytest.mark.parametrize("streamed", [False, True])
def test_texture_later_serialized_edit_replaces_png_preview_and_export(
    tmp_path, monkeypatch, replacement_format, streamed,
):
    from io import BytesIO
    from PIL import Image
    from AssetbundleUtils.PreviewWorker import preview_worker
    from tests.test_bundle_project_compatibility import texture_bundle

    source = texture_bundle(tmp_path)
    env = UnityPy_AOV.load(str(source))
    env.assets[0].objects.pop(207)
    reader = env.assets[0].objects[206]
    if replacement_format == "dump":
        # The fixture serializes the runtime 2022.3 Texture2D layout, while
        # this stock fallback schema has the older mipmap flag/no group name.
        # Adapt just these fixture nodes (the Dump code itself isn't mocked).
        from copy import copy
        from AssetbundleUtils.UnityPy_AOV.files import ObjectReader
        original_nodes = ObjectReader.get_typetree_nodes
        def runtime_nodes(obj, nodes=None):
            result = original_nodes(obj, nodes)
            if obj.type.name != "Texture2D":
                return result
            result = list(result)
            start = next(i for i, n in enumerate(result) if n.m_Name == "m_Name")
            limit = next(i for i, n in enumerate(result) if n.m_Name == "m_IgnoreMasterTextureLimit")
            flag = copy(result[limit])
            flag.m_Name = "m_IgnoreMipmapLimit"
            flag.m_MetaFlag |= 0x4000
            group = [copy(n) for n in result[start:start + 4]]
            group[0].m_Name = "m_MipmapLimitGroupName"
            result[limit:limit + 1] = [flag, *group]
            return result
        monkeypatch.setattr(ObjectReader, "get_typetree_nodes", runtime_nodes)
    texture = reader.read(False)
    red, green, blue = (230, 10, 20, 255), (20, 230, 30, 255), (20, 30, 230, 255)
    if not streamed:
        texture.image_data = bytes(red) * 4
        texture.save()
    source.write_bytes(env.file.save("lz4"))
    texture.m_Name = "replacement"
    if not streamed:
        texture.image_data = bytes(blue) * 4
    texture.save()
    replacement = tmp_path / ("replacement.dat" if replacement_format == "raw" else "replacement.json")
    if replacement_format == "raw":
        replacement.write_bytes(env.assets[0].objects[206].get_raw_data())
    else:
        export_typetree_dump(env.assets[0].objects[206], replacement)

    png = tmp_path / "green.png"
    Image.new("RGBA", (2, 2), green).save(png)
    b = Browser(source)
    b.target = b.env_list[0].assets[0].objects[206]
    class IdleWorker:
        def is_alive(self): return True
    b.preview_enabled, b.preview_generation, b.closed = True, 1, False
    b.preview_latest_generation = b.effect_root = None
    b.PREVIEW_PROCESS_COUNT = 1
    b.preview_processes = [IdleWorker()]
    b.preview_requests = [queue.Queue()]
    monkeypatch.setattr(ui, "show_dialog", lambda *args, **kwargs: True)

    def pixel(image_input):
        with Image.open(image_input) as image:
            return image.convert("RGBA").getpixel((0, 0))

    def verify_images(folder, color):
        output = tmp_path / folder
        output.mkdir()
        monkeypatch.setattr(ui, "askdirectory", lambda *args, **kwargs: str(output))
        b.export_texture()
        b.finish()
        assert pixel(next(output.glob("*.png"))) == color
        b._send_preview_request(1, 0, b.target)
        results = queue.Queue()
        requests = b.preview_requests[0]
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(preview_worker, requests, results, 0)
            try:
                _, kind, payload, err, _ = results.get(timeout=5)
            finally:
                requests.put(None)
                future.result(timeout=5)
        assert (kind, err) == ("image", None)
        assert pixel(BytesIO(payload)) == color

    try:
        monkeypatch.setattr(ui, "askopenfile", lambda *args, **kwargs: str(png))
        b.import_texture()
        b.finish()
        assert (0, 206) in b.texture_preview_overrides
        verify_images("before", green)

        monkeypatch.setattr(ui, "askopenfile", lambda *args, **kwargs: str(replacement))
        if replacement_format == "raw":
            b.import_raw()
        else:
            b.import_dump()
        b.finish()
        assert b.target.read(False).m_Name == "replacement"
        # With a stream, RAW/Dump contains a locator, not image bytes. Keep
        # the pixels edited in the live .resS, not the original disk pixels.
        expected = green if streamed else blue
        assert ((0, 206) in b.texture_preview_overrides) == streamed
        verify_images("after", expected)

        saved = tmp_path / "saved"
        saved.mkdir()
        monkeypatch.setattr(ui, "askdirectory", lambda *args, **kwargs: str(saved))
        b.save_bundles()
        assert b.finish()[2] is None
        result = UnityPy_AOV.load(str(saved / source.name))
        assert result.assets[0].objects[206].read(False).image.getpixel((0, 0)) == expected
    finally:
        b.executor.shutdown()
