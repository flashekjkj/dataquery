import sys
from pathlib import Path

# stdio子进程的sys.path[0]是run_server.py所在目录(mcp_server/)，
# 需要手动把backend目录加入path，才能import mcp_server/data/sql包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")