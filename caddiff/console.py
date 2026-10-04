"""控制台输出编码兜底——让非 ASCII 字符永远不会把程序打崩。

why：FreeCAD 自带的 Python 3.11 在中文 Windows 上 stdout 编码是 GBK，而输出里含
``mm³``（U+00B3）这类 GBK 之外的字符，``print`` 会抛 UnicodeEncodeError 让整个脚本
以退出码 1 结束——用户看到的是「程序坏了」，实际只是控制台编码不兼容。
实测：``make_moved_fixture.py`` 在 STP 已正确写出之后，仍因最后一行 print 崩掉。

策略（两种场景两种取舍，不搞一刀切）：
  - **控制台（isatty）**：保留本机编码，只把不可编码字符降级为 ``?``。中文 Windows 上
    中文照样正常显示，不会因为强转 UTF-8 变成乱码。
  - **管道 / 重定向**：改 UTF-8。被其他程序（CI、报告生成器）消费时编码稳定可预期。

调用点：每个**可独立执行的脚本**在 import 段末尾调一次。库函数不调——改全局 stdout
是副作用，只有入口才有资格做。
"""
import sys


def enable_utf8_output():
    """把 stdout/stderr 调成「不会因编码而崩」的状态。失败静默（不影响主流程）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # 被替换成非 TextIOWrapper（如测试框架的捕获对象）时无需处理
            pass
