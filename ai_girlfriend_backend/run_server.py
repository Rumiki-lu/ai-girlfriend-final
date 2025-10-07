import uvicorn
from app import app
import os
import sys

# --- 路径修正：确保 PyInstaller 能找到文件 ---
# PyInstaller 在 Windows 上运行异步服务器的推荐入口点

# 确定要运行的端口和host
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", 8000))

if __name__ == "__main__":
    # 使用 if __name__ == "__main__": 保护 uvicorn.run 是 PyInstaller 成功的关键
    # 它确保在 Windows 上正确启动多进程/线程环境
    try:
        print(f"Starting server on http://{HOST}:{PORT}")
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    except Exception as e:
        # 如果启动失败，将错误信息打印到控制台
        print(f"FATAL SERVER STARTUP ERROR: {e}", file=sys.stderr)
