# ArchiveStorageManager.py
from typing import Optional
from ..streams import EndianBinaryReader
from sm4 import SM4Key

class ArchiveStorageDecryptor:
    unknown_1: int
    index: bytes
    substitute: bytes = bytes(0x10)

    def __init__(self, reader: Optional[EndianBinaryReader] = None, key: Optional[bytes] = None, iv: Optional[bytes] = None, skip_blockinfo: bool = False):
        """
        reader: EndianBinaryReader if needed
        key/iv: optional SM4 key and IV
        skip_blockinfo: if True, will bypass encrypted blockInfo sections like AssetStudio
        """
        self.reader = reader
        self.BlockSM4Key = key or b"\x79\x7B\xCD\x5D\x7D\x7B\xB1\x11\x43\xD0\x0D\x71\x3C\xDA\xA8\x08"
        self.BlockSM4IV  = iv  or b"\x79\x7B\xCD\x5D\x7D\x7B\xB1\x11\x43\xD0\x0D\x71\x3C\xDA\xA8\x08"
        self.skip_blockinfo = skip_blockinfo

    def decrypt_block(self, data: bytes) -> bytes:
        """
        Decrypt a block with SM4. If skip_blockinfo is True, simply return the data as-is
        for encrypted blockInfo sections.
        """
        if self.skip_blockinfo:
            # Skip encrypted section: return raw data unmodified
            return data

        blocksize = (len(data) // 16) * 16
        unencrypted_part = data[-(len(data) % 16):] if len(data) % 16 != 0 else b''
        key0 = SM4Key(self.BlockSM4Key)
        decrypted_data = key0.decrypt(data[0:blocksize], initial=bytearray(self.BlockSM4IV))
        return decrypted_data + unencrypted_part

    def set_key(self, key: bytes, iv: Optional[bytes] = None):
        """
        Update SM4 key and optionally IV at runtime
        """
        self.BlockSM4Key = key
        if iv:
            self.BlockSM4IV = iv

