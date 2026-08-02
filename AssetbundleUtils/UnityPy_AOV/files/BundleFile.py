from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
import mmap
import os
import re
import tempfile
from typing import Tuple
from Crypto.Cipher import AES
from . import File
from ..enums import ArchiveFlags, ArchiveFlagsOld, CompressionFlags
from ..helpers import ArchiveStorageManager, CompressionHelper
from ..streams import EndianBinaryReader, EndianBinaryWriter

from .. import config

BlockInfo = namedtuple("BlockInfo", "flags tmp compressedSize uncompressedSize")
DirectoryInfoFS = namedtuple("DirectoryInfoFS", "size offset flags path")
reVersion = re.compile(r"(\d+)\.(\d+)\.(\d+)\w.+")


class BundleFile(File.File):
    # Small bundles stay in RAM for speed. Large decompressed block streams are
    # backed by a temporary mmap so parsing does not require one giant bytes
    # allocation plus all per-block outputs at the same time.
    MAX_IN_MEMORY_BLOCK_STREAM = 256 * 1024 * 1024
    WRITE_BLOCK_SIZE = 128 * 1024
    MAX_BLOCK_WORKERS = 4
    format: int
    is_changed: bool
    signature: str
    version_engine: str
    version_player: str
    dataflags: Tuple[ArchiveFlags, ArchiveFlagsOld]
    decryptor: ArchiveStorageManager.ArchiveStorageDecryptor = None
    special_storage_format: str = None

    def __init__(self, reader: EndianBinaryReader, parent: File, name: str = None):
        super().__init__(parent=parent, name=name)
        self.HeaderAESKey=b'\xE3\x05\x62\x14\xD6\x0A\x20\x25\x36\x96\x1B\x07\x74\xDC\x24\x02'
        self.HeaderAESIV=b'\x1D\x6E\xEB\x4C\x86\xA9\x45\x44\x45\x72\x12\x21\x2B\x43\x25\x2F'
        self.special_storage_format = None

        signature = self.signature = reader.read_string_to_null()
        self.version = reader.read_u_int()
        self.version_player = reader.read_string_to_null()
        self.version_engine = reader.read_string_to_null()

        if signature == "UnityArchive":
            raise NotImplementedError("BundleFile - UnityArchive")
        elif signature in ["UnityWeb", "UnityRaw"]:
            m_DirectoryInfo, blocksReader = self.read_web_raw(reader)
        elif signature == "UnityFS":
            m_DirectoryInfo, blocksReader = self.read_fs(reader)
        else:
            raise NotImplementedError(f"Unknown Bundle signature: {signature}")

        self.read_files(blocksReader, m_DirectoryInfo)

    def read_web_raw(self, reader: EndianBinaryReader):
        # def read_header_and_blocks_info(self, reader:EndianBinaryReader):
        version = self.version
        if version >= 4:
            _hash = reader.read_bytes(16)
            crc = reader.read_u_int()

        minimumStreamedBytes = reader.read_u_int()
        headerSize = reader.read_u_int()
        numberOfLevelsToDownloadBeforeStreaming = reader.read_u_int()
        levelCount = reader.read_int()
        reader.Position += 4 * 2 * (levelCount - 1)

        compressedSize = reader.read_u_int()
        uncompressedSize = reader.read_u_int()

        if version >= 2:
            completeFileSize = reader.read_u_int()

        if version >= 3:
            fileInfoHeaderSize = reader.read_u_int()

        reader.Position = headerSize

        uncompressedBytes = CompressionHelper.decompress_lzma(
            reader.read_bytes(compressedSize)
        )

        blocksReader = EndianBinaryReader(uncompressedBytes, offset=headerSize)
        nodesCount = blocksReader.read_int()
        m_DirectoryInfo = [
            File.DirectoryInfo(
                blocksReader.read_string_to_null(),  # path
                blocksReader.read_u_int(),  # offset
                blocksReader.read_u_int(),  # size
            )
            for _ in range(nodesCount)
        ]

        return m_DirectoryInfo, blocksReader

    def decryptHeader(self,data):
        data = bytes(data)
        #解密前header重組
        data=data[7::-1]+data[8:12][4::-1]+data[12:16][4::-1]  
        # 解密
        cipher = AES.new(self.HeaderAESKey, AES.MODE_CBC, self.HeaderAESIV)
        dataDecrypt = cipher.decrypt(data)
        return dataDecrypt

    def read_fs(self, reader: EndianBinaryReader):
        # 解密header
        bundleHeader = reader.read_bytes(16)
        


        self.dataflags = reader.read_u_int()

        version = self.get_version_tuple()
        # https://issuetracker.unity3d.com/issues/files-within-assetbundles-do-not-start-on-aligned-boundaries-breaking-patching-on-nintendo-switch
        # Unity CN introduced encryption before the alignment fix was introduced.
        # Unity CN used the same flag for the encryption as later on the alignment fix,
        # so we have to check the version to determine the correct flag set.
        if (
            version < (2020,)
            or (version[0] == 2020 and version < (2020, 3, 34))
            or (version[0] == 2021 and version < (2021, 3, 2))
            or (version[0] == 2022 and version < (2022, 1, 1))
        ):
            self.dataflags = ArchiveFlagsOld(self.dataflags)
        else:
            self.dataflags = ArchiveFlags(self.dataflags)

        if self.version >= 7:
            reader.align_stream(16)

        if self.dataflags & self.dataflags.UsesAssetBundleEncryption:
            bundleHeader = self.decryptHeader(bundleHeader)
            size=int.from_bytes(bundleHeader[0x0:0x8], 'little')
            compressedSize=int.from_bytes(bundleHeader[0x8:0xc], 'little')
            uncompressedSize=int.from_bytes(bundleHeader[0xc:0x10], 'little')
        else:

            size=int.from_bytes(bundleHeader[0x0:0x8], 'big')
            compressedSize=int.from_bytes(bundleHeader[0x8:0xc], 'big')
            uncompressedSize=int.from_bytes(bundleHeader[0xc:0x10], 'big')
        self._data_flags = self.dataflags

        if (
            self.dataflags & self.dataflags.UsesAssetBundleEncryption
            and size > reader.Length
        ):
            raise ValueError(
                f"Encrypted UnityFS declared size exceeds the file: "
                f"{size} > {reader.Length}"
            )
        self.declared_size = size
        self.trailing_storage_size = max(0, reader.Length - size)

        if compressedSize < 0 or uncompressedSize < 0:
            raise ValueError("Negative UnityFS block-info size")
        if compressedSize > reader.Length:
            raise ValueError(
                f"UnityFS block info is larger than the bundle: "
                f"{compressedSize} > {reader.Length}"
            )

        start = reader.Position
        encrypted_blocks_info = bool(
            self.dataflags & self.dataflags.UsesAssetBundleEncryption
        )
        encrypted_blocks_info_at_end = bool(
            encrypted_blocks_info
            and self.dataflags & ArchiveFlags.BlocksInfoAtTheEnd
        )
        if encrypted_blocks_info:
            self.decryptor = ArchiveStorageManager.ArchiveStorageDecryptor(reader)

        if (
            self.dataflags & ArchiveFlags.BlocksInfoAtTheEnd
        ):  # kArchiveBlocksInfoAtTheEnd
            info_position = reader.Length - compressedSize
            if info_position < start:
                raise ValueError("UnityFS block info overlaps the file header")
            reader.Position = info_position
            blocksInfoBytes = self._read_exact(reader, compressedSize, "block info")
            reader.Position = start
        else:  # 0x40 kArchiveBlocksAndDirectoryInfoCombined
            blocksInfoBytes = self._read_exact(reader, compressedSize, "block info")

        # AOV Unity 2022 variants keep their LZMA/LZ4 data blocks ordinary, but
        # protect BlocksInfo with SM4-CBC.  BlocksInfo can be either prefixed or
        # stored at EOF; decrypt it exactly once before normal decompression.
        # Ordinary bundles never enter this flag-gated branch.
        if encrypted_blocks_info:
            blocksInfoBytes = self.decryptor.decrypt_block(blocksInfoBytes)
            compression_name = {
                1: "lzma", 2: "lz4", 3: "lz4hc",
            }.get(int(self.dataflags) & 0x3F, "plain")
            location = "at-end" if encrypted_blocks_info_at_end else "prefix"
            self.special_storage_format = (
                f"aov-sm4-blockinfo-{location}-{compression_name}"
            )

        blocksInfoBytes = self.decompress_data(
            blocksInfoBytes,
            uncompressedSize,
            int(self.dataflags) & (~0x200 if encrypted_blocks_info else -1),
        )
        
        blocksInfoReader = EndianBinaryReader(blocksInfoBytes, endian = ">",offset=start)

        uncompressedDataHash = blocksInfoReader.read_bytes(16)
        blocksInfoCount = blocksInfoReader.read_int()
        if blocksInfoCount < 0 or blocksInfoCount > (blocksInfoReader.Length - 20) // 12:
            raise ValueError(f"Invalid UnityFS block count: {blocksInfoCount}")
        # aov的blockinfo 順序 (flag compressedSize uncompressedSize)
        # flag 後方會多 2bytes 
        m_BlocksInfo = [
            BlockInfo(
                blocksInfoReader.read_u_short(), #flag
                blocksInfoReader.read_u_short(), #unknow 通常是 00 00
                blocksInfoReader.read_u_int(),  # compressedSize
                blocksInfoReader.read_u_int(),  # uncompressedSize

            )
            
            for _ in range(blocksInfoCount)
        ]
        
        nodesCount = blocksInfoReader.read_int()
        if nodesCount < 0 or nodesCount > blocksInfoReader.Length // 21:
            raise ValueError(f"Invalid UnityFS directory count: {nodesCount}")
        m_DirectoryInfo = [
            DirectoryInfoFS(
                blocksInfoReader.read_long(),  # size
                blocksInfoReader.read_long(),  # offset
                blocksInfoReader.read_u_int(),  # flags
                blocksInfoReader.read_string_to_null(),  # path
            )
            for _ in range(nodesCount)
        ]
        #print(m_DirectoryInfo)

        if m_BlocksInfo:
            self._block_info_flags = m_BlocksInfo[0].flags

        if (
            isinstance(self.dataflags, ArchiveFlags)
            and self.dataflags & ArchiveFlags.BlockInfoNeedPaddingAtStart
        ):
            reader.align_stream(16)

        total_uncompressed = sum(block.uncompressedSize for block in m_BlocksInfo)
        for node in m_DirectoryInfo:
            if node.offset < 0 or node.size < 0 or node.offset + node.size > total_uncompressed:
                raise ValueError(
                    f"Bundle entry {node.path!r} range [{node.offset}, "
                    f"{node.offset + node.size}) exceeds the {total_uncompressed}-byte block stream"
                )

        blocksReader = self._read_block_stream(
            reader,
            m_BlocksInfo,
            total_uncompressed,
            blocksInfoReader.real_offset(),
        )
        return m_DirectoryInfo, blocksReader

    @staticmethod
    def _read_exact(reader, size, label):
        data = reader.read_bytes(size)
        if len(data) != size:
            raise EOFError(f"Truncated {label}: expected {size} bytes, got {len(data)}")
        return data

    def _read_block_stream(self, reader, blocks, total_size, base_offset):
        if total_size < 0:
            raise ValueError("Negative decompressed UnityFS size")

        # Avoid duplicating a single large decompression result. Multi-block
        # bundles still use the preallocated/mmap path below to eliminate the
        # old join-time full-stream copy.
        if len(blocks) == 1:
            block = blocks[0]
            compressed = self._read_exact(
                reader, block.compressedSize, "compressed block 0"
            )
            decompressed = self.decompress_data(
                compressed, block.uncompressedSize, block.flags, 0
            )
            if (
                self.special_storage_format
                and len(decompressed) > block.uncompressedSize
                and not any(decompressed[block.uncompressedSize:])
            ):
                # A known protected AOV variant stores the block size one byte
                # short while the decoded suffix is zero padding. Directory
                # ranges end at the declared size, so discard only verified
                # all-zero overflow instead of weakening ordinary validation.
                decompressed = decompressed[:block.uncompressedSize]
            if len(decompressed) != total_size:
                raise ValueError(
                    f"Block 0 decompressed to {len(decompressed)} bytes; expected {total_size}"
                )
            return EndianBinaryReader(decompressed, offset=base_offset)

        if total_size > self.MAX_IN_MEMORY_BLOCK_STREAM:
            temp_file = tempfile.TemporaryFile(prefix="unitypy_aov_")
            temp_file.truncate(total_size)
            storage = mmap.mmap(temp_file.fileno(), total_size, access=mmap.ACCESS_WRITE)
            self._block_temp_file = temp_file
            self._block_mmap = storage
        else:
            storage = bytearray(total_size)

        output = memoryview(storage)
        block_offsets = []
        write_offset = 0
        for block in blocks:
            block_offsets.append(write_offset)
            write_offset += block.uncompressedSize

        # The stream must be read in order, but independent compressed blocks can
        # be decoded concurrently. A rolling window prevents large bundles from
        # multiplying peak memory by the number of blocks.
        worker_count = min(
            self.MAX_BLOCK_WORKERS,
            max(1, os.cpu_count() or 1),
            len(blocks),
        )
        max_in_flight = max(2, worker_count * 2)

        def decompress_block(index, block, compressed):
            data = self.decompress_data(
                compressed, block.uncompressedSize, block.flags, index
            )
            if (
                self.special_storage_format
                and len(data) > block.uncompressedSize
                and not any(data[block.uncompressedSize:])
            ):
                data = data[:block.uncompressedSize]
            actual_size = len(data)
            if actual_size != block.uncompressedSize:
                raise ValueError(
                    f"Block {index} decompressed to {actual_size} bytes; "
                    f"expected {block.uncompressedSize}"
                )
            return index, data

        def store_result(future):
            index, data = future.result()
            start = block_offsets[index]
            output[start : start + len(data)] = data

        with ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="UnityFSBlock"
        ) as executor:
            pending = []
            for index, block in enumerate(blocks):
                compressed = self._read_exact(
                    reader, block.compressedSize, f"compressed block {index}"
                )
                pending.append(
                    executor.submit(decompress_block, index, block, compressed)
                )
                if len(pending) >= max_in_flight:
                    store_result(pending.pop(0))

            for future in pending:
                store_result(future)

        if write_offset != total_size:
            raise ValueError(
                f"UnityFS block stream size mismatch: wrote {write_offset}, expected {total_size}"
            )
        return EndianBinaryReader(output, offset=base_offset)

    def save(self, packer=None):
        """
        Rewrites the BundleFile and returns it as bytes object.

        packer:
            can be either one of the following strings
            or tuple consisting of (block_info_flag, data_flag)
            allowed strings:
                none - no compression, default, safest bet
                lz4 - lz4 compression
                original - uses the original flags
                aov-fingerprint-2 - SM4 prefix BlocksInfo + LZ4HC (0x643)
                aov-fingerprint-3 - SM4 prefix BlocksInfo + LZMA (0x641)
                aov-fingerprint-1 - SM4 EOF BlocksInfo + LZMA (0x6C1)
        """
        # file_header
        #     signature    (string_to_null)
        #     format        (int)
        #     version_player    (string_to_null)
        #     version_engine    (string_to_null)
        writer = EndianBinaryWriter()

        writer.write_string_to_null(self.signature)
        writer.write_u_int(self.version)
        writer.write_string_to_null(self.version_player)
        writer.write_string_to_null(self.version_engine)

        if self.signature == "UnityArchive":
            raise NotImplementedError("BundleFile - UnityArchive")
        elif self.signature in ["UnityWeb", "UnityRaw"]:
            raise NotImplementedError(
                "Saving Unity Web and Raw bundles isn't supported yet"
            )
            # self.save_web_raw(writer)
        elif self.signature == "UnityFS":
            if not packer or packer == "none":
                self.save_fs(writer, 64, 64)
            elif packer == "original":
                self.save_fs(
                    writer,
                    data_flag=self._data_flags,
                    block_info_flag=self._block_info_flags,
                )
            elif packer == "lz4":
                self.save_fs(writer, data_flag=194, block_info_flag=2)
            elif packer in ("aov-fingerprint-2", "fingerprint-2"):
                self.save_fs(
                    writer,
                    data_flag=0x643,
                    block_info_flag=3,
                    encrypt_header=True,
                    encrypt_blocks_info=True,
                    single_data_block=True,
                )
            elif packer in ("aov-fingerprint-3", "fingerprint-3"):
                self.save_fs(
                    writer,
                    data_flag=0x641,
                    block_info_flag=1,
                    encrypt_header=True,
                    encrypt_blocks_info=True,
                    single_data_block=True,
                )
            elif packer in ("aov-fingerprint-1", "fingerprint-1"):
                self.save_fs(
                    writer,
                    data_flag=0x6C1,
                    block_info_flag=0x41,
                    encrypt_header=True,
                    encrypt_blocks_info=True,
                    single_data_block=True,
                )
            elif isinstance(packer, tuple):
                self.save_fs(writer, *packer)
            else:
                raise NotImplementedError("UnityFS - Packer:", packer)
        return writer.bytes

    def save_fs(
        self,
        writer: EndianBinaryWriter,
        data_flag: int,
        block_info_flag: int,
        *,
        encrypt_header: bool = False,
        encrypt_blocks_info: bool = False,
        single_data_block: bool = False,
    ):
        # header
        # compressed blockinfo (block details & directionary)
        # compressed assets

        # 0b1000000 / 0b11000000 | 64 / 192 - uncompressed
        # 0b11000010 | 194 - lz4
        # block_info_flag

        # 0 / 0b1000000 | 0 / 64 - uncompressed
        # 0b1   | 1 - lzma
        # 0b10  | 2 - lz4
        # 0b11  | 3 - lz4hc [not implemented]
        # 0b100 | 4 - lzham [not implemented]
        # data_flag

        # header:
        #     bundle_size        (long)
        #     compressed_size    (int)
        #     uncompressed_size    (int)
        #     flag                (int)
        #     ?padding?            (bool)
        #   This will be written at the end,
        #   because the size can only be calculated after the data compression,

        # block_info:
        #     *flag & 0x80 ? at the end : right after header
        #     *decompression via flag & 0x3F
        #     *read compressed_size -> uncompressed_size
        #     0x10 offset
        #     *read blocks infos of the data stream
        #     count            (int)
        #     (
        #         uncompressed_size(uint)
        #         compressed_size (uint)
        #         flag(short)
        #     )
        #     *decompression via info.flag & 0x3F

        #     *afterwards the file positions
        #     file_count        (int)
        #     (
        #         offset    (long)
        #         size        (long)
        #         flag        (int)
        #         name        (string_to_null)
        #     )

        # file list & file data
        # prep nodes and build up block data
        data_writer = EndianBinaryWriter()
        files = [
            (
                name,
                f.flags,
                data_writer.write_bytes(
                    f.bytes
                    if isinstance(f, (EndianBinaryReader, EndianBinaryWriter))
                    else f.save()
                ),
            )
            for name, f in self.files.items()
        ]

        file_data = data_writer.bytes
        data_writer.dispose()
        switch = block_info_flag & 0x3F
        compressed_file_data = bytearray()
        block_records = []
        chunk_size = max(1, len(file_data)) if single_data_block else self.WRITE_BLOCK_SIZE
        for chunk_start in range(0, len(file_data), chunk_size):
            chunk = file_data[chunk_start : chunk_start + chunk_size]
            if switch == 1:  # LZMA
                compressed_chunk = CompressionHelper.compress_lzma(chunk)
            elif switch in [2, 3]:  # LZ4, LZ4HC
                compressed_chunk = CompressionHelper.compress_lz4(chunk)
            elif switch == 4:  # LZHAM
                raise NotImplementedError
            else:
                compressed_chunk = chunk
            block_records.append(
                (block_info_flag, len(compressed_chunk), len(chunk))
            )
            compressed_file_data.extend(compressed_chunk)
        file_data = compressed_file_data

        # write the block_info
        # uncompressedDataHash
        block_writer = EndianBinaryWriter(b"\x00" * 0x10)
        # data block info
        # block count
        block_writer.write_int(len(block_records))

        # blockInfo 恢復aov排序模式
        # flag
        for flags, compressed_size, uncompressed_size in block_records:
            block_writer.write_u_short(flags)
        # 未知 2 bytes
            block_writer.write_u_short(0)
        # compressed size
            block_writer.write_u_int(compressed_size)
        # uncompressed size
            block_writer.write_u_int(uncompressed_size)

        #data_flag = 0x43
        # file block info
        if not data_flag & 0x40:
            raise NotImplementedError(
                "UnityPy always writes DirectoryInfo, so data_flag must include 0x40"
            )
        # file count
        block_writer.write_int(len(files))
        offset = 0
        # size offset順序對調
        for f_name, f_flag, f_len in files:
            # size
            block_writer.write_long(f_len)
            # offset
            block_writer.write_long(offset)
            # flag
            block_writer.write_u_int(f_flag)
            # name
            block_writer.write_string_to_null(f_name)
            # 
            offset += f_len

        # compress the block data
        block_data = block_writer.bytes
        block_writer.dispose()

        uncompressed_block_data_size = len(block_data)

        switch = data_flag & 0x3F
        if switch == 1:  # LZMA
            block_data = CompressionHelper.compress_lzma(block_data)
        elif switch in [2, 3]:  # LZ4, LZ4HC
            block_data = CompressionHelper.compress_lz4(block_data)
        elif switch == 4:  # LZHAM
            raise NotImplementedError

        compressed_block_data_size = len(block_data)

        if encrypt_blocks_info:
            block_data = ArchiveStorageManager.ArchiveStorageDecryptor().encrypt_block(
                block_data
            )

        # write the header info
        ## file size - 0 for now, will be set at the end
        writer_header_pos = writer.Position
        if encrypt_header:
            writer.write_bytes(b"\x00" * 16)
        else:
            writer.write_u_long(7)
            # compressed blockInfoBytes size
            writer.write_u_int(compressed_block_data_size)
            # uncompressed size
            writer.write_u_int(uncompressed_block_data_size)
        # compression and file layout flag
        writer.write_u_int(data_flag)

        if self.version >= 7:
            # UnityFS\x00 - 8
            # size 8
            # comp sizes 4+4
            # flag 4
            # sum : 28 -> +8 alignment
            writer.align_stream(16)

        if data_flag & 0x80:  # at end of file
            if data_flag & 0x200:
                writer.align_stream(16)
            writer.write(file_data)
            writer.write(block_data)
        else:
            writer.write(block_data)
            if data_flag & 0x200:
                writer.align_stream(16)
            writer.write(file_data)

        writer_end_pos = writer.Position
        writer.Position = writer_header_pos

        if encrypt_header:
            header = (
                int(writer_end_pos).to_bytes(8, "little", signed=False)
                + int(compressed_block_data_size).to_bytes(4, "little", signed=False)
                + int(uncompressed_block_data_size).to_bytes(4, "little", signed=False)
            )
            encrypted = AES.new(
                self.HeaderAESKey, AES.MODE_CBC, self.HeaderAESIV
            ).encrypt(header)
            encrypted = (
                encrypted[:8][::-1]
                + encrypted[8:12][::-1]
                + encrypted[12:16][::-1]
            )
            writer.write_bytes(encrypted)
            writer.Position = writer_end_pos
            return
        
        # correct file size
        writer.write_u_long(writer_end_pos)

        # 拚header
        """
        header = b''
        header += writer_end_pos.to_bytes(8, 'big', signed=False)
        header += compressed_block_data_size.to_bytes(4, 'big', signed=False)
        header += uncompressed_block_data_size.to_bytes(4, 'big', signed=False)
        # 加密
        cipher = AES.new(self.HeaderAESKey, AES.MODE_CBC, self.HeaderAESIV)
        enc_header = cipher.encrypt(header)
        # 順序修正
        enc_header = enc_header[:8][::-1] + enc_header[8:12][::-1] + enc_header[12:16][::-1]
        
        writer.write_bytes(enc_header)
        """
        writer.Position = writer_end_pos

    def decompress_data(
        self, compressed_data: bytes, uncompressed_size: int, flags: int, index: int = 0
    ) -> bytes:
        """
        Parameters
        ----------
        compressed_data : bytes
            The compressed data.
        uncompressed_size : int
            The uncompressed size of the data.
        flags : int
            The flags of the data.

        Returns
        -------
        bytes
            The decompressed data."""
        comp_flag = flags & ArchiveFlags.CompressionTypeMask
    
        if comp_flag == CompressionFlags.LZMA:  # LZMA
            return CompressionHelper.decompress_lzma(compressed_data)
        elif comp_flag in [CompressionFlags.LZ4, CompressionFlags.LZ4HC]:  # LZ4, LZ4HC
            # In modern UnityFS (including Unity 2022), 0x200 is
            # BlockInfoNeedPaddingAtStart, not data encryption. Only protected
            # AOV layouts create a decryptor; ordinary 0x243 bundles must pass
            # their LZ4HC bytes straight to the decoder.
            if flags & 0x200 and self.decryptor is not None:
                compressed_data = self.decryptor.decrypt_block(compressed_data)
            return CompressionHelper.decompress_lz4(compressed_data, uncompressed_size)
        elif comp_flag == CompressionFlags.LZHAM:  # LZHAM
            raise NotImplementedError("LZHAM decompression not implemented")
        else:
            return compressed_data

    def get_version_tuple(self) -> Tuple[int, int, int]:
        """Returns the version as a tuple."""
        version = self.version_engine
        if not version or version == "0.0.0":
            version = config.get_fallback_version()
        return tuple(map(int, reVersion.match(version).groups()))
