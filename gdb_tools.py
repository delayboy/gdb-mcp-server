#!/usr/bin/env python3
"""GDB MCP工具函数"""

import logging
import re
import time
import traceback
from typing import Dict, Any
from comm_methods.gdb_communicator import GdbCommunicator

logger = logging.getLogger('gdb-mcp-server.tools')
gdb_communicator = None

def init_communicator():
    """初始化GDB通信器"""
    global gdb_communicator
    if gdb_communicator is None:
        gdb_communicator = GdbCommunicator()
        logger.info("通信器初始化完成")
    return gdb_communicator
def _scan_gdb_processes():
    """扫描系统中正在运行的 GDB 进程，过滤掉 Python 启动脚本与 gdbserver。

    返回 [{pid, tty, cmd}, ...] 列表。
    """
    import subprocess
    processes = []
    try:
        output = subprocess.check_output(
            ["ps", "-eo", "pid,tty,command"], text=True
        ).strip()
    except Exception as e:
        logger.error(f"扫描GDB进程出错: {str(e)}")
        return processes

    for line in output.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        pid = parts[0]
        tty = parts[1]
        cmdline = ' '.join(parts[2:])

        if 'gdb' not in cmdline.lower():
            continue
        if '.py' in cmdline or 'python' in cmdline.lower():
            continue

        processes.append({
            "pid": pid,
            "tty": tty if tty != '?' else "未知",
            "cmd": cmdline,
        })
    return processes

def sys_find_or_start_gdb(random_string="dummy") -> Dict[str, Any]:
    """查找正在运行的 GDB 进程；若系统中一个都没有，则自动启动一个新的 gdb 会话。

    调用成功后，系统中至少存在一个可交互的 gdb 进程（找不到就自己拉起一个）。
    返回:
      - processes: GDB 进程列表 [{pid, tty, cmd}, ...]
      - gdb_pid:   首个有效 GDB 进程的 PID（可直接传给 sys_attach_or_start_gdb）
      - created:   True 表示本次是新创建的会话，False 表示找到了已有进程
    """
    try:
        processes = _scan_gdb_processes()
        created = False

        if not processes:
            # 系统中没有任何 gdb → 通过通信器自动创建一个 tmux gdb 会话
            comm = init_communicator()
            comm.attach_to_gdb()  # Linux: 找不到则 _create_session 自动启动
            created = True
            # 等待 gdb 进程注册到 ps，带几次重试
            for _ in range(6):
                time.sleep(0.5)
                processes = _scan_gdb_processes()
                if processes:
                    break

        gdb_pid = processes[0]["pid"] if processes else None

        if processes:
            formatted = [f"PID: {p['pid']}, TTY: {p['tty']}, CMD: {p['cmd']}" for p in processes]
            action = "已自动创建并启动" if created else "找到"
            result_message = (
                f"{action} {len(processes)} 个 GDB 进程，"
                f"推荐使用 gdb_pid={gdb_pid}（用 sys_attach_or_start_gdb 附加后即可调试）:\n"
                + "\n".join(formatted)
            )
        else:
            result_message = "未能找到或启动任何 GDB 进程，请检查 tmux/gdb 是否可用。"

        return {
            "success": bool(processes),
            "output": str(processes),
            "formatted_result": result_message,
            "processes": processes,
            "gdb_pid": gdb_pid,
            "created": created,
            "has_output": True,
        }
    except Exception as e:
        logger.error(f"查找/启动 GDB 出错: {str(e)}")
        return {
            "success": False,
            "output": f"错误: {str(e)}",
            "formatted_result": f"查找/启动 GDB 出错: {str(e)}",
            "processes": [],
            "gdb_pid": None,
            "created": False,
            "has_output": True,
        }

def sys_attach_or_start_gdb(gdb_pid=None, tty_device=None) -> Dict[str, Any]:
    """附加到一个 GDB 会话（绑定调试通道），以便后续执行 gdb_* 命令。

    - 指定 gdb_pid: 附加到该 PID。
    - 不指定 gdb_pid/tty_device: 自动查找正在运行的 GDB，取首个有效 PID 附加；
      若一个都没有，则自动启动一个新会话并附加（find_or_start 的能力）。
    调用成功后即可直接使用 gdb_execute_command 等命令。
    """
    comm = init_communicator()
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)

    is_attached = False
    # 没给 PID/TTY → 自动查找或启动一个 gdb，并复用其首个有效 PID
    if gdb_pid is None and tty_device is None:
        info = sys_find_or_start_gdb()
        resolved_pid = info.get("gdb_pid")
        if resolved_pid is None:
            # 一个都没找到也起不来 → 直接走失败分支
            pass
        else:
            gdb_pid = resolved_pid
            # find_or_start 在创建会话时已经把通信器连接好了，无需重复附加
            if comm.connected:
                is_attached = True
            else:
                is_attached = comm.attach_to_gdb(gdb_pid)
    else:
        is_attached = comm.attach_to_gdb(gdb_pid, tty_device)

    if is_attached:
        if gdb_pid:
            result_message = f"成功附加到GDB进程 PID: {gdb_pid}"
        elif tty_device:
            result_message = f"成功附加到GDB终端 TTY: {tty_device}"
        else:
            result_message = "成功附加到GDB会话"
    else:
        if gdb_pid:
            result_message = f"附加到GDB进程 PID: {gdb_pid} 失败"
        elif tty_device:
            result_message = f"附加到GDB终端 TTY: {tty_device} 失败"
        else:
            result_message = "附加失败：未找到也无法启动 GDB 会话"

    return {
        "success": is_attached,
        "output": f"PID: {gdb_pid}, TTY: {tty_device}",
        "formatted_result": result_message,
        "is_attached": is_attached,
        "gdb_pid": gdb_pid,
        "tty_device": tty_device,
        "has_output": True,
    }

# GDB相关工具
def gdb_execute_command(command, gdb_pid=None) -> Dict[str, Any]:
    """执行GDB命令"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
    
    comm = init_communicator()
    success, output = comm.execute_command(command, gdb_pid)
    
    cmd_type = command.split()[0] if " " in command else command
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    
    result_message = f"执行命令: {command}\n{output}" if success else f"命令执行失败: {output}"
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": result_message,
        "command_type": cmd_type,
        "has_output": not command_sent_but_no_output
    }

def gdb_set_breakpoint(location, gdb_pid=None) -> Dict[str, Any]:
    """设置断点"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)

    comm = init_communicator()
    # Hex addresses must use * prefix: break *0x7ffff... not break 0x7ffff...
    # GDB treats bare hex as a function name lookup and prompts for pending breakpoint.
    if re.match(r'^0[xX][0-9a-fA-F]+$', location.strip()):
        location = f"*{location.strip()}"
    success, output = comm.execute_command(f"break {location}", gdb_pid)
    
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    blocked = "阻塞" in output and "中断" in output
    
    if success:
        result_message = f"设置断点 '{location}': {output}"
    else:
        result_message = f"设置断点失败: {output}"
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": result_message,
        "has_output": not command_sent_but_no_output,
        "blocked": blocked
    }

def gdb_delete_breakpoint(number, gdb_pid=None) -> Dict[str, Any]:
    """删除断点"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
        
    comm = init_communicator()
    success, output = comm.execute_command(f"delete {number}", gdb_pid)
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": f"删除断点 #{number}: {output}" if success else f"删除断点失败: {output}",
        "has_output": not command_sent_but_no_output
    }

def gdb_step(gdb_pid=None) -> Dict[str, Any]:
    """单步执行"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
    comm = init_communicator()
    success, output = comm.execute_command("step", gdb_pid)
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": f"单步执行: {output}" if success else f"单步执行失败: {output}",
        "has_output": not command_sent_but_no_output
    }

def gdb_next(gdb_pid=None) -> Dict[str, Any]:
    """下一步执行"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
    comm = init_communicator()
    success, output = comm.execute_command("next", gdb_pid)
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": f"下一步执行: {output}" if success else f"下一步执行失败: {output}",
        "has_output": not command_sent_but_no_output
    }

def gdb_finish(gdb_pid=None) -> Dict[str, Any]:
    """运行至函数返回"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
    comm = init_communicator()
    success, output = comm.execute_command("finish", gdb_pid)
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": f"运行至函数返回: {output}" if success else f"运行至函数返回失败: {output}",
        "has_output": not command_sent_but_no_output
    }

def gdb_continue_bounded_3s(gdb_pid=None) -> Dict[str, Any]:
    """继续执行(continue)，带3秒安全网：若程序3秒内未自行停下则自动发Ctrl-C强制中断。

    适合短期状态采样/监控；不要用于需要程序连续运行超过3秒的场景（等用户操作/慢超时/
    时序敏感崩溃），那种场景请用 gdb_wait_stop，或直接 tmux send-keys continue。
    """
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)

    comm = init_communicator()
    success, output = comm.execute_command("continue", gdb_pid)
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    blocked = "阻塞" in output and "中断" in output

    return {
        "success": success,
        "output": output,
        "formatted_result": f"continue(3秒兜底): {output}" if success else f"continue失败: {output}",
        "has_output": not command_sent_but_no_output,
        "blocked": blocked
    }

def gdb_get_registers(gdb_pid=None) -> Dict[str, Any]:
    """获取寄存器值"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
    comm = init_communicator()
    success, output = comm.execute_command("info registers", gdb_pid)
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": f"寄存器值: {output}" if success else f"获取寄存器值失败: {output}",
        "command": "info registers",
        "has_output": not command_sent_but_no_output
    }

def gdb_examine_memory(address, count="10", format_type="x", gdb_pid=None) -> Dict[str, Any]:
    """检查内存"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
    count = str(count)
    command = f"x/{count}{format_type} {address}"
    
    comm = init_communicator()
    success, output = comm.execute_command(command, gdb_pid)
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": f"检查内存 {address}: {output}" if success else f"检查内存失败: {output}",
        "command": command,
        "has_output": not command_sent_but_no_output
    }

def gdb_get_stack(gdb_pid=None) -> Dict[str, Any]:
    """获取堆栈信息"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
    comm = init_communicator()
    success, output = comm.execute_command("backtrace", gdb_pid)
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": f"堆栈回溯: {output}" if success else f"获取堆栈失败: {output}",
        "command": "backtrace",
        "has_output": not command_sent_but_no_output
    }

def gdb_get_locals(gdb_pid=None) -> Dict[str, Any]:
    """获取局部变量"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
    comm = init_communicator()
    success, output = comm.execute_command("info locals", gdb_pid)
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": f"局部变量: {output}" if success else f"获取局部变量失败: {output}",
        "command": "info locals",
        "has_output": not command_sent_but_no_output
    }

def gdb_disassemble(location="", gdb_pid=None) -> Dict[str, Any]:
    """反汇编代码"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
    command = "disassemble" if not location else f"disassemble {location}"
    
    comm = init_communicator()
    success, output = comm.execute_command(command, gdb_pid)
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": f"反汇编{' '+location if location else ''}: {output}" if success else f"反汇编失败: {output}",
        "command": command,
        "has_output": not command_sent_but_no_output
    }

def gdb_connect_remote(target_address, gdb_pid=None) -> Dict[str, Any]:
    """连接到远程调试目标"""
    if gdb_pid is not None:
        gdb_pid = str(gdb_pid)
        
    command = f"target remote {target_address}"
    comm = init_communicator()
    success, output = comm.execute_command(command, gdb_pid)
    
    command_sent_but_no_output = ("通过键盘事件发送" in output or "请在GDB终端中" in output)
    blocked = "阻塞" in output and "中断" in output
    
    result_message = f"连接远程目标 '{target_address}': {output}" if success else f"连接失败: {output}"
    
    return {
        "success": success, 
        "output": output, 
        "formatted_result": result_message,
        "command": command,
        "target_address": target_address,
        "has_output": not command_sent_but_no_output,
        "blocked": blocked
    }

def gdb_wait_stop(timeout=30) -> Dict[str, Any]:
    """被动等待程序自行停下（崩溃SIGSEGV/断点/信号），不发Ctrl-C，不扰动时序。

    用于发出continue后等待并捕获崩溃/断点命中。轮询直到gdb回到提示符或超时，
    返回pane尾部现场。timeout默认30秒。
    """
    try:
        comm = init_communicator()
        r = comm.wait_stop(timeout)
        elapsed = r.get("elapsed", 0.0)
        scene = r.get("scene", "")
        if r.get("stopped"):
            msg = f"已停下(耗时{elapsed}s)\n{scene}"
        else:
            msg = f"等待超时({elapsed}s)，程序可能仍在运行。\n{scene}"
        return {
            "success": r.get("success", False),
            "was_blocked": r.get("was_blocked", False),
            "stopped": r.get("stopped", False),
            "elapsed": elapsed,
            "scene": scene,
            "formatted_result": msg,
        }
    except Exception as e:
        logger.error(f"wait_stop出错: {str(e)}")
        return {"success": False, "was_blocked": False, "stopped": False, "elapsed": 0.0,
                "scene": f"出错: {str(e)}", "formatted_result": f"wait_stop出错: {str(e)}"}


def gdb_try_interrupt(timeout=10) -> Dict[str, Any]:
    """主动发Ctrl-C中断正在运行的程序（若已停下则为空操作），并如实返回 was_blocked(调用前是否在跑) 与停止现场。

    兼具"叫停程序"与"如实探测是否在跑"两种用途，替代旧的被动 check_blocked
    （旧实现读的是可能失真的内部标志）。timeout默认10秒。
    """
    try:
        comm = init_communicator()
        r = comm.try_interrupt(timeout)
        elapsed = r.get("elapsed", 0.0)
        scene = r.get("scene", "")
        pre = "程序在跑" if r.get("was_blocked") else "程序已停"
        if r.get("stopped"):
            msg = f"调用前{pre}；已停下(耗时{elapsed}s)。\n{scene}"
        else:
            msg = f"调用前{pre}；未在超时({elapsed}s)内回到提示符。\n{scene}"
        return {
            "success": r.get("success", False),
            "was_blocked": r.get("was_blocked", False),
            "stopped": r.get("stopped", False),
            "elapsed": elapsed,
            "scene": scene,
            "formatted_result": msg,
        }
    except Exception as e:
        logger.error(f"try_interrupt出错: {str(e)}")
        return {"success": False, "was_blocked": False, "stopped": False, "elapsed": 0.0,
                "scene": f"出错: {str(e)}", "formatted_result": f"try_interrupt出错: {str(e)}"}


def gdb_run_async(command="continue") -> Dict[str, Any]:
    """发出continue让程序自由运行并立即返回(不轮询、不发Ctrl-C、不强制中断)。

    用于启动一段需要连续运行超过3秒的调试(等用户GUI操作/慢速网络超时/时序敏感的崩溃)。
    若程序已在运行则不重复发送(避免命令堆积)。发出后用 gdb_wait_stop 观察停止/捕获崩溃，
    或用 gdb_try_interrupt 叫停。
    """
    try:
        comm = init_communicator()
        r = comm.run_async(command)
        return {
            "success": r.get("success", False),
            "running": r.get("running", False),
            "command": r.get("command", command),
            "scene": r.get("scene", ""),
            "formatted_result": r.get("scene", ""),
        }
    except Exception as e:
        logger.error(f"run_async出错: {str(e)}")
        return {"success": False, "running": False, "command": command,
                "scene": f"出错: {str(e)}", "formatted_result": f"run_async出错: {str(e)}"} 