#!/usr/bin/env python3
"""Tmux通信方法 - 通过tmux终端直接与GDB通信"""

import logging
import re
import subprocess
import time
from typing import Tuple

logger = logging.getLogger("gdb-mcp-server.tmux_comm")


class TmuxCommunicator:
    """使用tmux终端与GDB通信的类"""

    def __init__(self):
        self.tmux_session_name = None
        self.last_command_time = 0.0
        self.is_blocked = False
        self._gdb_pattern = re.compile(r"\bgdb(?:-multiarch)?\b", re.IGNORECASE)
        logger.info("Tmux通信器初始化")
        self._check_dependencies()

    def _check_dependencies(self):
        """检查tmux是否可用"""
        try:
            subprocess.check_call(
                ["which", "tmux"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            logger.info("找到tmux工具")
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.warning("未找到tmux，请安装: sudo apt-get install tmux")

    def find_gdb_window(self):
        """查找包含GDB的tmux窗口"""
        try:
            cmd = [
                "tmux",
                "list-panes",
                "-a",
                "-F",
                "#{session_name}  ->  #{pane_current_command}",
            ]
            output = subprocess.check_output(cmd, text=True).strip()
            if not output:
                return False

            for line in output.splitlines():
                if "->" not in line:
                    continue
                session_part, command_part = line.split("->", 1)
                session_name = session_part.strip()
                command = command_part.strip()
                if not session_name or not command:
                    continue

                command_lower = command.lower()
                if "gdbserver" in command_lower:
                    continue

                if self._gdb_pattern.search(command):
                    self.tmux_session_name = session_name
                    return True
            return False
        except Exception as exc:
            logger.error(f"查找GDB窗口失败: {exc}")
            return False

    def start_gdb(self, executable):
        """启动或附加到 tmux 中的 gdb 会话"""
        cmd = ["tmux", "has-session", "-t", "gdb_session"]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            logger.info("发现 tmux 会话")
            if not self.find_gdb_window():
                command = f"gdb -q {executable}"
                start_cmd = ["tmux", "send-keys", "-t", "gdb_session", command, "Enter"]
                cmd_result = subprocess.run(start_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if cmd_result.returncode == 0:
                    self.tmux_session_name = "gdb_session"
                    return True, f"启动gdb调试{executable}成功"
                return False, f"启动gdb调试{executable}失败"
            return True, "已经存在运行中的gdb，请直接附加"

        logger.info("未发现 tmux 会话，尝试在新的终端中启动")
        tmux_result = subprocess.run(
            ["gnome-terminal", "--", "tmux", "new-session", "-A", "-s", "gdb_session"],
            capture_output=True,
            text=True,
        )
        if tmux_result.returncode != 0:
            return False, f"启动gdb调试{executable}失败"

        command = f"gdb -q {executable}"
        start_cmd = ["tmux", "send-keys", "-t", "gdb_session", command, "Enter"]
        cmd_result = subprocess.run(start_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if cmd_result.returncode == 0:
            self.tmux_session_name = "gdb_session"
            return True, f"启动gdb调试{executable}成功"
        return False, f"启动gdb调试{executable}失败"

    def check_gdb_blocked(self):
        """报告GDB是否阻塞"""
        if not self.is_blocked:
            return {"is_blocked": False, "running_time": 0, "status": "GDB处于交互状态"}
        running_time = time.time() - self.last_command_time
        return {
            "is_blocked": True,
            "running_time": running_time,
            "status": f"GDB运行中，已运行 {running_time:.1f} 秒",
        }

    # ---- 长跑生命周期：wait_stop(被动等停) / try_interrupt(主动叫停) ----
    # 这些方法直接读 pane 判断是否回到 (gdb) 提示符，不依赖 is_blocked 标志，
    # 因此即使 continue 是从外部（如直接 tmux send-keys）发出的，也能如实反映状态。

    def _capture_pane(self, target):
        """读取整个 tmux pane 的历史缓冲文本"""
        cmd = ["tmux", "capture-pane", "-p", "-t", target, "-S", "-", "-E", "-"]
        return subprocess.check_output(cmd, text=True, timeout=3)

    def _at_prompt(self, pane):
        """判断 gdb 是否已回到 (gdb) 提示符（即程序已停下）"""
        lines = [ln for ln in pane.splitlines() if ln.strip() != ""]
        if not lines:
            return False
        return re.search(r"\(gdb\)\s*$", lines[-1]) is not None

    def _stop_scene(self, pane, n=80):
        """返回 pane 尾部 n 行作为停止现场"""
        return "\n".join(pane.splitlines()[-n:]).strip()

    def wait_stop(self, timeout=30):
        """被动等待程序自行停下（崩溃/断点/信号），不发 Ctrl-C，不扰动时序。

        返回 dict:
          - success: 通信是否成功
          - was_blocked: 调用前程序是否在跑（未在提示符）
          - stopped: 超时内是否已停下
          - scene: pane 尾部现场
          - elapsed: 耗时(秒)
        """
        if not self._require_session():
            return {"success": False, "was_blocked": False, "stopped": False,
                    "scene": "未找到GDB的tmux会话，请先启动或附加", "elapsed": 0.0}
        target = self.tmux_session_name
        start = time.time()
        try:
            pane = self._capture_pane(target)
        except Exception as exc:
            return {"success": False, "was_blocked": False, "stopped": False,
                    "scene": f"读取pane失败: {exc}", "elapsed": 0.0}
        was_blocked = not self._at_prompt(pane)
        stopped = self._at_prompt(pane)
        deadline = start + timeout
        while not stopped and time.time() < deadline:
            time.sleep(0.5)
            try:
                pane = self._capture_pane(target)
            except Exception:
                pass
            stopped = self._at_prompt(pane)
        if stopped:
            self.is_blocked = False
        return {"success": True, "was_blocked": was_blocked, "stopped": stopped,
                "scene": self._stop_scene(pane), "elapsed": round(time.time() - start, 1)}

    def try_interrupt(self, timeout=10):
        """主动发 Ctrl-C 叫停程序，并如实报告调用前是否在跑。

        若程序已在提示符（已停下），Ctrl-C 为空操作，was_blocked=False。
        返回 dict 同 wait_stop。
        """
        if not self._require_session():
            return {"success": False, "was_blocked": False, "stopped": False,
                    "scene": "未找到GDB的tmux会话，请先启动或附加", "elapsed": 0.0}
        target = self.tmux_session_name
        start = time.time()
        try:
            pane = self._capture_pane(target)
        except Exception as exc:
            return {"success": False, "was_blocked": False, "stopped": False,
                    "scene": f"读取pane失败: {exc}", "elapsed": 0.0}
        was_blocked = not self._at_prompt(pane)
        try:
            subprocess.check_output(["tmux", "send-keys", "-t", target, "C-c"],
                                    text=True, timeout=3)
        except Exception as exc:
            return {"success": False, "was_blocked": was_blocked, "stopped": False,
                    "scene": f"发送C-c失败: {exc}", "elapsed": 0.0}
        stopped = False
        deadline = start + timeout
        while time.time() < deadline:
            time.sleep(0.3)
            try:
                pane = self._capture_pane(target)
            except Exception:
                pass
            if self._at_prompt(pane):
                stopped = True
                break
        if stopped:
            self.is_blocked = False
        else:
            self.is_blocked = was_blocked
            self.last_command_time = start
        return {"success": True, "was_blocked": was_blocked, "stopped": stopped,
                "scene": self._stop_scene(pane), "elapsed": round(time.time() - start, 1)}

    def run_async(self, command="continue"):
        """发出命令(默认 continue)并立即返回，不轮询、不发 Ctrl-C。让程序自由运行。

        若程序已在运行(未在提示符)，则不重复发送，避免命令堆积到缓冲区。
        返回 dict: success, running(发送后是否预期在跑), scene, command。
        """
        if not self._require_session():
            return {"success": False, "running": False,
                    "scene": "未找到GDB的tmux会话，请先启动或附加", "command": command}
        target = self.tmux_session_name
        try:
            pane = self._capture_pane(target)
        except Exception as exc:
            return {"success": False, "running": False,
                    "scene": f"读取pane失败: {exc}", "command": command}
        if not self._at_prompt(pane):
            return {"success": False, "running": True,
                    "scene": "程序已在运行，未重复发送(避免命令堆积)。如需重启先 gdb_try_interrupt 叫停。",
                    "command": command}
        try:
            subprocess.check_output(["tmux", "send-keys", "-t", target, command, "Enter"],
                                    text=True, timeout=3)
        except Exception as exc:
            return {"success": False, "running": False,
                    "scene": f"发送失败: {exc}", "command": command}
        self.is_blocked = True
        self.last_command_time = time.time()
        return {"success": True, "running": True,
                "scene": f"已发送 '{command}'，程序开始运行。用 gdb_wait_stop 观察/捕获停止，用 gdb_try_interrupt 叫停。",
                "command": command}

    def _require_session(self):
        if self.tmux_session_name:
            return True
        if self.find_gdb_window():
            self._init_session()
            return True
        # Auto-create: no existing gdb session found, start one
        return self._create_session()

    DEFAULT_SESSION = "navidatagdb"

    def _create_session(self):
        """Create a detached tmux session with gdb -q, then init it."""
        try:
            subprocess.check_output(
                ["tmux", "new-session", "-d", "-s", self.DEFAULT_SESSION, "gdb", "-q"],
                text=True, timeout=5,
            )
            logger.info(f"自动创建tmux会话 {self.DEFAULT_SESSION}")
            self.tmux_session_name = self.DEFAULT_SESSION
            time.sleep(0.5)  # let gdb start up
            self._init_session()
            return True
        except subprocess.CalledProcessError:
            # Session may already exist under a different name; try attaching anyway
            logger.warning("自动创建tmux会话失败，尝试查找已有会话")
            if self.find_gdb_window():
                self._init_session()
                return True
            return False

    def _init_session(self):
        """Send initialization commands to a newly found/created gdb session."""
        if not self.tmux_session_name:
            return
        try:
            # Disable pagination so long output never blocks on --Type <RET> for more--
            # Disable confirmation so delete/break never prompt for (y or n)
            init_cmds = ["set pagination off", "set confirm off"]
            for cmd in init_cmds:
                subprocess.check_output(
                    ["tmux", "send-keys", "-t", self.tmux_session_name, cmd, "Enter"],
                    text=True, timeout=3,
                )
                time.sleep(0.3)
            logger.info(f"已发送初始化命令: {', '.join(init_cmds)}")
        except Exception as exc:
            logger.warning(f"初始化gdb会话失败: {exc}")

    def execute_command(self, command) -> Tuple[bool, str]:
        """使用tmux方式执行GDB命令"""
        try:
            if not self._require_session():
                return False, "未找到GDB的tmux会话，请先启动或附加"

            target = self.tmux_session_name
            time_id = int(time.time())
            output_marker = f"<<<GDB_OUTPUT_START_{time_id}>>>"
            end_marker = f"<<<GDB_OUTPUT_END_{time_id}>>>"
            might_block = command.strip() in {"c", "continue", "run", "r"} or "target remote" in command

            cmds = [
                ["tmux", "send-keys", "-t", target, f"echo {output_marker}", "Enter"],
                ["tmux", "send-keys", "-t", target, command, "Enter"],
            ]
            for tmux_cmd in cmds:
                subprocess.check_output(tmux_cmd, text=True, timeout=3)

            capture_cmd = ["tmux", "capture-pane", "-p", "-t", target, "-S", "-", "-E", "-"]

            # Wait for gdb prompt to return (handles long-output commands like
            # "info proc mappings" that take more than 1 second to complete).
            # We poll the pane until the (gdb) prompt reappears, up to 15 seconds.
            prompt_returned = False
            pane_text = ""
            prompt_deadline = time.time() + 15
            while time.time() < prompt_deadline:
                time.sleep(0.4)
                try:
                    pane_text = subprocess.check_output(capture_cmd, text=True, timeout=3).strip()
                except Exception:
                    continue
                lines = [ln for ln in pane_text.splitlines() if ln.strip()]
                if lines and re.search(r"\(gdb\)\s*$", lines[-1]):
                    prompt_returned = True
                    break

            if not prompt_returned:
                # GDB prompt never returned — command may be blocking (e.g. continue/run)
                if might_block:
                    interrupt_cmds = [
                        ["tmux", "send-keys", "-t", target, "C-c"],
                        ["tmux", "send-keys", "-t", target, "echo <<<GDB_INTERRUPTED>>>", "Enter"],
                    ]
                    for cmd in interrupt_cmds:
                        subprocess.check_output(cmd, text=True, timeout=3)
                    self.is_blocked = True
                    self.last_command_time = time.time()
                    return True, "命令执行阻塞，已发送中断信号。"
                return False, "命令执行超时(15s)，gdb提示符未返回。"

            # Prompt is back — send end marker and capture final pane
            end_cmd = ["tmux", "send-keys", "-t", target, f"echo {end_marker}", "Enter"]
            subprocess.check_output(end_cmd, text=True, timeout=3)
            time.sleep(0.3)
            content = subprocess.check_output(capture_cmd, text=True, timeout=3).strip()

            self.is_blocked = False

            if output_marker in content and end_marker in content:
                data = content.split(output_marker, 1)[1]
                output_value = data.split(end_marker, 1)[0]
                cleaned = output_value.replace(command, "", 1).strip()
                return True, cleaned

            # Markers not found — output was too long and scrolled out of tmux buffer
            # Return what we have from the pane (after command echo) with a hint
            hint = (f"⚠ 输出文本过长，起始标记已滚出tmux缓冲区导致截断。"
                    f"建议使用 pipe 命令筛选，例如: pipe {command} | grep <关键词>")
            # Try to salvage whatever is between the command and end_marker
            partial = content
            if end_marker in partial:
                partial = partial.split(end_marker, 1)[0]
            # Strip the command echo line itself if present
            partial_lines = [l for l in partial.splitlines() if l.strip()]
            if partial_lines:
                return True, hint + "\n---\n" + "\n".join(partial_lines)
            return True, hint

        except Exception as exc:
            logger.error(f"执行命令失败: {exc}")
            return False, f"执行命令失败: {exc}"

