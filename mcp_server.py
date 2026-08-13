#!/usr/bin/env python3
"""GDB MCP Server - 实现Model Context Protocol标准，为GDB调试提供AI辅助功能"""

import logging
import traceback
from typing import Dict
from fastmcp import FastMCP
import gdb_tools
import os
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('gdb-mcp-server')
os.environ["FASTMCP_LOG_LEVEL"] = "INFO"
mcp = FastMCP("GDB")

@mcp.tool(name="sys_find_gdb_processes")
def sys_find_gdb_processes(random_string="dummy") -> Dict:
    """查找系统中运行的所有GDB进程"""
    return gdb_tools.sys_find_gdb_processes(random_string)

@mcp.tool(name="sys_attach_to_gdb")
def sys_attach_to_gdb(gdb_pid=None, tty_device=None) -> Dict:
    """附加到现有的GDB进程"""
    return gdb_tools.sys_attach_to_gdb(gdb_pid, tty_device)


@mcp.tool(name="gdb_execute_command")
def gdb_execute_command(command, gdb_pid=None) -> Dict:
    """执行GDB命令"""
    return gdb_tools.gdb_execute_command(command, gdb_pid)

@mcp.tool(name="gdb_set_breakpoint")
def gdb_set_breakpoint(location, gdb_pid=None) -> Dict:
    """设置断点"""
    return gdb_tools.gdb_set_breakpoint(location, gdb_pid)

@mcp.tool(name="gdb_delete_breakpoint")
def gdb_delete_breakpoint(number, gdb_pid=None) -> Dict:
    """删除断点"""
    return gdb_tools.gdb_delete_breakpoint(number, gdb_pid)

@mcp.tool(name="gdb_step")
def gdb_step(gdb_pid=None) -> Dict:
    """单步执行"""
    return gdb_tools.gdb_step(gdb_pid)

@mcp.tool(name="gdb_next")
def gdb_next(gdb_pid=None) -> Dict:
    """下一步执行"""
    return gdb_tools.gdb_next(gdb_pid)

@mcp.tool(name="gdb_finish")
def gdb_finish(gdb_pid=None) -> Dict:
    """运行至函数返回"""
    return gdb_tools.gdb_finish(gdb_pid)

@mcp.tool(name="gdb_continue_bounded_3s")
def gdb_continue_bounded_3s(gdb_pid=None) -> Dict:
    """继续执行(continue)，带3秒安全网：3秒内未自行停下则自动Ctrl-C强制中断。适合短期采样/监控；长跑场景用gdb_wait_stop。"""
    return gdb_tools.gdb_continue_bounded_3s(gdb_pid)

@mcp.tool(name="gdb_get_registers")
def gdb_get_registers(gdb_pid=None) -> Dict:
    """获取寄存器值"""
    return gdb_tools.gdb_get_registers(gdb_pid)

@mcp.tool(name="gdb_examine_memory")
def gdb_examine_memory(address, count="10", format_type="x", gdb_pid=None) -> Dict:
    """检查内存"""
    return gdb_tools.gdb_examine_memory(address, count, format_type, gdb_pid)

@mcp.tool(name="gdb_get_stack")
def gdb_get_stack(gdb_pid=None) -> Dict:
    """获取堆栈信息"""
    return gdb_tools.gdb_get_stack(gdb_pid)

@mcp.tool(name="gdb_get_locals")
def gdb_get_locals(gdb_pid=None) -> Dict:
    """获取局部变量"""
    return gdb_tools.gdb_get_locals(gdb_pid)

@mcp.tool(name="gdb_disassemble")
def gdb_disassemble(location="", gdb_pid=None) -> Dict:
    """反汇编代码"""
    return gdb_tools.gdb_disassemble(location, gdb_pid)

@mcp.tool(name="gdb_connect_remote")
def gdb_connect_remote(target_address, gdb_pid=None) -> Dict:
    """连接到远程调试目标"""
    return gdb_tools.gdb_connect_remote(target_address, gdb_pid)

@mcp.tool(name="gdb_wait_stop")
def gdb_wait_stop(timeout: int = 30) -> Dict:
    """被动等待程序自行停下(崩溃SIGSEGV/断点/信号)，不发Ctrl-C，不扰动时序；返回pane尾部现场。捕获崩溃用这个。"""
    return gdb_tools.gdb_wait_stop(timeout)


@mcp.tool(name="gdb_try_interrupt")
def gdb_try_interrupt(timeout: int = 10) -> Dict:
    """主动发Ctrl-C叫停正在运行的程序(已停下则空操作)，并如实返回 was_blocked(调用前是否在跑) 与现场。叫停/探测是否在跑用这个。"""
    return gdb_tools.gdb_try_interrupt(timeout)


@mcp.tool(name="gdb_run_async")
def gdb_run_async(command: str = "continue") -> Dict:
    """发出continue让程序自由运行并立即返回(不轮询/不中断)。长跑场景用这个发车，再用gdb_wait_stop观察或gdb_try_interrupt叫停。"""
    return gdb_tools.gdb_run_async(command)

if __name__ == "__main__":
    logger.info("启动GDB MCP服务器")
    try:
        mcp.run()
    except KeyboardInterrupt:
        logger.info("GDB MCP服务器已终止")
    except Exception as e:
        logger.error(f"GDB MCP服务器错误: {str(e)}")
        logger.error(traceback.format_exc())