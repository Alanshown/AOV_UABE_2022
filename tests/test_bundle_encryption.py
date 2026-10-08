"""Regression coverage for protected bundles saved with their original flags."""
import pytest

from AssetbundleUtils.UnityPy_AOV.files import BundleFile
from AssetbundleUtils.UnityPy_AOV.streams import EndianBinaryReader, EndianBinaryWriter
from tests.fixtures import bundle_bytes, mesh_object


def read_bundle(data):
    return BundleFile(EndianBinaryReader(data), None)


def payloads(bundle):
    return {name: file.save() if hasattr(file, 'save') else file.bytes
            for name, file in bundle.files.items()}


@pytest.mark.parametrize('packer', [
    'aov-fingerprint-1', 'aov-fingerprint-2', 'aov-fingerprint-3',
])
def test_encrypted_original_save_reload(packer):
    source = read_bundle(bundle_bytes(mesh_object(), packer))
    expected = payloads(source)
    assert source.decryptor is not None
    for _ in range(2):
        saved = source.save('original')
        reloaded = read_bundle(saved)
        assert reloaded.declared_size == len(saved)
        assert reloaded.dataflags == source.dataflags
        assert reloaded.special_storage_format == source.special_storage_format
        assert payloads(reloaded) == expected
        source = reloaded


@pytest.mark.parametrize('flags', [0x243, 0x2C3, 0x643, 0x6C3, 0x641, 0x6C1])
@pytest.mark.parametrize('version', [6, 7])
def test_tuple_flags_match_written_encryption(flags, version):
    source = read_bundle(bundle_bytes(mesh_object()))
    source.version = version
    saved = source.save((flags, 3))
    reloaded = read_bundle(saved)
    assert int(reloaded.dataflags) == flags
    assert bool(reloaded.decryptor) == bool(flags & 0x400)
    assert payloads(reloaded) == payloads(source)


@pytest.mark.parametrize('packer', ['none', 'lz4'])
def test_explicit_plain_save_removes_encryption(packer):
    source = read_bundle(bundle_bytes(mesh_object(), 'aov-fingerprint-1'))
    reloaded = read_bundle(source.save(packer))
    assert reloaded.decryptor is None
    assert not (int(reloaded.dataflags) & 0x400)
    assert payloads(reloaded) == payloads(source)


@pytest.mark.parametrize('flags', [0x243, 0x2C3])
@pytest.mark.parametrize('version', [6, 7])
def test_old_unity_encryption_bit_is_not_padding(flags, version):
    source = read_bundle(bundle_bytes(mesh_object()))
    source.version_engine = '2020.3.33f1'
    source.version = version
    reloaded = read_bundle(source.save((flags, 3)))
    assert reloaded.decryptor is not None
    assert payloads(reloaded) == payloads(source)
    assert payloads(read_bundle(reloaded.save('original'))) == payloads(source)


@pytest.mark.parametrize('packer', [
    'aov-fingerprint-1', 'aov-fingerprint-2', 'aov-fingerprint-3',
])
def test_encrypted_original_multiblock_preserves_edited_resources(packer):
    source = read_bundle(bundle_bytes(mesh_object(), packer))
    resource = EndianBinaryReader(bytes(range(256)) * 1600)
    resource.flags = 0
    source.files['test.resS'] = resource
    reloaded = read_bundle(source.save('original'))
    assert payloads(reloaded) == payloads(source)
    assert reloaded.dataflags == source.dataflags


@pytest.mark.parametrize('flags', [0x643, 0x6C3])
def test_encrypted_lz4_data_blocks_roundtrip(flags):
    source = read_bundle(bundle_bytes(mesh_object()))
    reloaded = read_bundle(source.save((flags, 0x203)))
    assert reloaded._block_info_flags == 0x203
    assert payloads(reloaded) == payloads(source)
    assert payloads(read_bundle(reloaded.save('original'))) == payloads(source)


@pytest.mark.parametrize('flags,options', [
    (0x643, {'encrypt_header': False}),
    (0x643, {'encrypt_blocks_info': False}),
    (0x243, {'encrypt_header': True}),
    (0x243, {'encrypt_blocks_info': True}),
])
def test_inconsistent_encryption_options_rejected(flags, options):
    source = read_bundle(bundle_bytes(mesh_object()))
    with pytest.raises(ValueError, match='must match the archive flags'):
        source.save_fs(EndianBinaryWriter(), flags, 3, **options)
