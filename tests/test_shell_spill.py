"""SpillWriter 的环形缓冲与溢出落盘测试。

覆盖设计文档 §6.2 的全部用例，外加几条实现期发现的边界。
"""

from __future__ import annotations

import os
import sys

import pytest

from nono_agent.shell.spill import SpillWriter


def _read(path) -> bytes:
    return path.read_bytes() if path is not None else b""


class TestUnderLimit:
    def test_returns_everything(self, tmp_path):
        writer = SpillWriter(max_bytes=100, spill_path=tmp_path / "s.log")
        writer.write(b"hello ")
        writer.write(b"world")
        delta = writer.read_from(0)
        assert delta.text == "hello world"
        assert delta.next_offset == 11
        assert delta.start_offset == 0
        assert delta.lossy is False
        assert delta.spill_path is None  # 没溢出就不该有落盘路径
        assert delta.truncated is False

    def test_incremental_reads_never_repeat(self, tmp_path):
        writer = SpillWriter(max_bytes=100, spill_path=tmp_path / "s.log")
        writer.write(b"abcdef")
        first = writer.read_from(0)
        second = writer.read_from(first.next_offset)
        assert first.text == "abcdef"
        assert second.text == ""
        assert second.next_offset == first.next_offset

    def test_utf8_decoding(self, tmp_path):
        writer = SpillWriter(max_bytes=100, spill_path=tmp_path / "s.log")
        writer.write("中文".encode())
        assert writer.read_from(0).text == "中文"

    def test_empty_write_is_noop(self, tmp_path):
        writer = SpillWriter(max_bytes=100, spill_path=tmp_path / "s.log")
        writer.write(b"")
        assert writer.read_from(0).text == ""


class TestOverflow:
    def test_ring_keeps_tail_and_reports_lossy(self, tmp_path):
        writer = SpillWriter(max_bytes=4, spill_path=tmp_path / "s.log")
        writer.write(b"abcdefgh")
        delta = writer.read_from(0)
        assert delta.text == "efgh"  # 回吐能给的尾部，不是空
        assert delta.lossy is True
        assert delta.start_offset == 4  # 告知文本实际从哪开始
        assert delta.next_offset == 8
        assert delta.spill_path == tmp_path / "s.log"

    def test_overflow_is_spilled_immediately(self, tmp_path):
        # 落盘发生在写入时而非截断时：模型拿到路径的瞬间文件已完整
        path = tmp_path / "s.log"
        writer = SpillWriter(max_bytes=4, spill_path=path)
        writer.write(b"abcdefgh")
        assert _read(path) == b"abcd"

    def test_cursor_is_continuous_across_ring_and_file(self, tmp_path):
        # 同一游标在内存环与落盘文件之间无缝衔接。必须在**边写边读**下验证：
        # 消费式游标的契约是「及时读」，事后从 0 读已经越过的那段会标 lossy。
        path = tmp_path / "s.log"
        writer = SpillWriter(max_bytes=4, spill_path=path)

        writer.write(b"abcd")
        first = writer.read_from(0)
        assert (first.text, first.lossy) == ("abcd", False)

        writer.write(b"efgh")  # 此刻 abcd 被挤到文件里
        second = writer.read_from(first.next_offset)
        assert (second.text, second.lossy) == ("efgh", False)
        assert first.next_offset == second.start_offset
        assert _read(path) == b"abcd"

    def test_lossy_read_advances_cursor_past_gap(self, tmp_path):
        path = tmp_path / "s.log"
        writer = SpillWriter(max_bytes=4, spill_path=path)
        writer.write(b"abcdefgh")
        delta = writer.read_from(0)
        assert delta.lossy is True
        # 丢了 [0,4)，游标必须已经推进到能给的起点，否则下次读会原地打转
        assert delta.start_offset == 4
        assert writer.read_from(delta.next_offset).text == ""

    def test_no_spill_path_means_pure_drop(self, tmp_path):
        writer = SpillWriter(max_bytes=4, spill_path=None)
        writer.write(b"abcdefgh")
        delta = writer.read_from(0)
        assert delta.text == "efgh"
        # 无处落盘时，被淘汰的字节仍必须推进偏移空间，否则游标与文本实际起点
        # 不符，下一次增量读会静默错位。
        assert delta.lossy is True
        assert delta.start_offset == 4
        assert delta.next_offset == 8
        assert delta.spill_path is None
        assert delta.truncated is True  # 数据真的丢了，调用方必须知道
        assert writer.read_from(delta.next_offset).text == ""


class TestSpillLimit:
    def test_stops_writing_at_max_spill_bytes(self, tmp_path):
        path = tmp_path / "s.log"
        writer = SpillWriter(max_bytes=4, spill_path=path, max_spill_bytes=6)
        writer.write(b"abcdefgh")  # 溢出 4 字节
        writer.write(b"ijkl")  # 再溢出 4 字节，累计 8 > 6
        assert _read(path) == b"abcd"  # 只落盘了第一批
        delta = writer.read_from(0)
        assert delta.truncated is True
        assert delta.text == b"ijkl".decode()

    def test_nested_parent_is_created(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "s.log"
        writer = SpillWriter(max_bytes=2, spill_path=path)
        writer.write(b"abcdef")
        assert path.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX 权限位")
    def test_file_permissions_are_owner_only(self, tmp_path):
        # 对齐 session/transcript.py：落盘即挡住同机其它用户
        path = tmp_path / "s.log"
        writer = SpillWriter(max_bytes=2, spill_path=path)
        writer.write(b"abcdef")
        assert os.stat(path).st_mode & 0o777 == 0o600


class TestLifecycle:
    def test_close_returns_path_only_if_spilled(self, tmp_path):
        path = tmp_path / "s.log"
        clean = SpillWriter(max_bytes=10, spill_path=path)
        clean.write(b"abc")
        assert clean.close() is None

        dirty = SpillWriter(max_bytes=2, spill_path=path)
        dirty.write(b"abcdef")
        assert dirty.close() == path

    def test_write_after_close_is_ignored(self, tmp_path):
        writer = SpillWriter(max_bytes=100, spill_path=tmp_path / "s.log")
        writer.write(b"abc")
        writer.close()
        writer.write(b"def")
        assert writer.read_from(0).text == "abc"

    def test_total_bytes_tracks_all_destinations(self, tmp_path):
        writer = SpillWriter(max_bytes=4, spill_path=tmp_path / "s.log")
        writer.write(b"abcdef")
        assert writer.total_bytes == 6  # 2 落盘 + 4 在环

    def test_concurrent_writes_do_not_corrupt(self, tmp_path):
        import threading

        writer = SpillWriter(max_bytes=64, spill_path=tmp_path / "s.log")

        def worker(marker: bytes) -> None:
            for _ in range(200):
                writer.write(marker)

        threads = [threading.Thread(target=worker, args=(bytes([i]),)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert writer.total_bytes == 800


class TestDecoding:
    """解码必须在**字节层**完成。

    回归防护：解码链曾经被拆成「SpillWriter 先按 utf-8 + replace 解一遍，工具层
    再对替换字符回退」。后一步拿到的已经是被 U+FFFD 覆盖的 str，回退只会把替换
    字符重新编码一遍 —— 中文 Windows 上按 CP936 输出的子进程整段变乱码。
    """

    def test_gbk_bytes_survive_the_ring(self, tmp_path):
        writer = SpillWriter(max_bytes=100, spill_path=tmp_path / "s.log")
        writer.write("驱动器 D 中的卷是 新加卷".encode("gbk"))
        text = writer.read_from(0).text
        assert text == "驱动器 D 中的卷是 新加卷"
        assert "�" not in text

    def test_utf8_still_wins_over_locale(self, tmp_path):
        """本机默认编码是 GBK 时，utf-8 输出不能被它抢走。"""
        writer = SpillWriter(max_bytes=100, spill_path=tmp_path / "s.log")
        writer.write("中文测试".encode())
        assert writer.read_from(0).text == "中文测试"

    def test_truncated_tail_keeps_complete_chars(self, tmp_path):
        """尾部被读断时只显示完整部分 —— 半个汉字会让整段被误判成 GBK。"""
        writer = SpillWriter(max_bytes=100, spill_path=tmp_path / "s.log")
        writer.write("你好".encode()[:-1])
        assert writer.read_from(0).text == "你"

    def test_cut_head_still_decodes(self, tmp_path):
        """环形缓冲保留尾部：切点落在字符中间时读到的仍要可读。

        切点可能落在多字节字符内部，解码没有唯一正解；这里只保证不出现替换符
        —— 编码归属由 :func:`decode_output` 的候选链按整段判定。
        """
        writer = SpillWriter(max_bytes=4, spill_path=tmp_path / "s.log")
        writer.write("abc中文".encode())
        delta = writer.read_from(0)
        assert delta.lossy is True
        assert "�" not in delta.text

    def test_undecodable_bytes_keep_replacement_chars(self, tmp_path):
        """无解时不静默丢弃：宁可给替换符，也要让模型看见有输出。"""
        writer = SpillWriter(max_bytes=100, spill_path=tmp_path / "s.log")
        writer.write(b"\x80\x81\x82")
        assert writer.read_from(0).text


class TestValidation:
    def test_rejects_non_positive_max_bytes(self, tmp_path):
        with pytest.raises(ValueError, match="max_bytes must be positive"):
            SpillWriter(max_bytes=0, spill_path=tmp_path / "s.log")

    def test_rejects_negative_spill_limit(self, tmp_path):
        with pytest.raises(ValueError, match="max_spill_bytes"):
            SpillWriter(max_bytes=10, spill_path=tmp_path / "s.log", max_spill_bytes=-1)
