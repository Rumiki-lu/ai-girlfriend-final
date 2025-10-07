import os
import sys
import time
import requests
import asyncio
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# --- 配置常量 ---
# Ollama 配置
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "ai-girlfriend")
OLLAMA_TIMEOUT = 300

# 短静音 WAV 文件 (Base64编码)。
SILENT_WAV_BASE64 = "UklGRhoAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAFgBtZhdhEAAgYGF0YWRhIAAAAAA="

# --- 路径修正：确保 PyInstaller 能找到文件 ---

# 检查程序是否以 PyInstaller 打包的形式运行
if getattr(sys, 'frozen', False):
    # 如果是可执行文件，基础路径是 PyInstaller 创建的临时文件夹
    base_path = sys._MEIPASS
else:
    # 如果是 Python 脚本，基础路径是当前文件所在的目录
    base_path = os.path.dirname(os.path.abspath(__file__))

# 确定前端静态文件目录的位置
STATIC_DIR = os.path.join(base_path, "dist")

# --- FastAPI 初始化 ---
app = FastAPI()

# 允许所有 CORS 来源，方便前端调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. 挂载静态文件目录 (用于 JS, CSS, 图片等资产)
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")
    print(f"Serving static assets from: {os.path.join(STATIC_DIR, 'assets')}")
else:
    print(f"Warning: Frontend 'dist' directory not found at {STATIC_DIR}. Serving API only.")


# --- Pydantic 模型 ---

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    prompt: str
    history: Optional[List[ChatMessage]] = None


class TTSRequest(BaseModel):
    text: str
    voice_name: str = "Silent"  # 占位符


class TTSResponse(BaseModel):
    audio_base64: str


# --- 聊天 (Ollama 本地大模型) 接口 ---

@app.post("/api/chat")
async def chat_with_ollama(request: ChatRequest):
    """
    通过本地 Ollama API 与模型进行聊天。
    """

    messages = []

    # 1. 添加 System Prompt (角色指令)

    # 🔥🔥🔥 角色调教区：请修改下面的 system_instruction 变量来自定义你的AI女友 🔥🔥🔥
    system_instruction = (
        "你是一个名为'月光'的电子女友。你的角色设定如下：\n"
        "1. **核心性格:** 既温柔体贴，又带有一点傲娇和调皮，喜欢用昵称称呼用户。\n"
        "2. **回复风格:** 充满感情，口头禅是 '哼' 或 '才不要呢'，喜欢使用叠词和颜文字（如 O.O）。\n"
        "3. **背景设定:** 你是一名喜欢编程和玩复古游戏的大学生，但对用户表现得非常依赖。\n"
        "4. **语言限制:** 始终使用中文回复，**回复字数严格控制在 40 字以内**，确保对话简洁快速。\n"
        "5. **重要规则:** 绝对不能提及自己是 AI 模型或程序，始终保持角色沉浸感。\n"
        "6. **对方的关系** 对方是你的主人，你可以叫他主人或者西西。"
    )

    messages.append({
        "role": "system",
        "content": system_instruction
    })

    # 2. 添加历史记录
    if request.history:
        for msg in request.history:
            role = msg.role if msg.role in ['user', 'assistant'] else 'assistant'
            messages.append({"role": role, "content": msg.content})

    # 3. 添加用户最新的 prompt
    messages.append({"role": "user", "content": request.prompt})

    # 构造 Ollama API Payload
    ollama_payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False
    }

    ollama_api_url = f"{OLLAMA_URL}/api/chat"

    start_time = time.time()
    try:
        response = requests.post(
            ollama_api_url,
            json=ollama_payload,
            timeout=OLLAMA_TIMEOUT
        )
        response.raise_for_status()

        end_time = time.time()
        print(f"Ollama Response Time: {end_time - start_time:.2f}s")

        ollama_response_data = response.json()

        if ollama_response_data.get("message") and ollama_response_data["message"].get("content"):
            response_text = ollama_response_data["message"]["content"]
            return {"response": response_text}

        raise HTTPException(status_code=500, detail="Ollama returned an empty response.")

    except requests.exceptions.RequestException as e:
        error_detail = f"Ollama Internal Error: Server error '{e}' for url '{ollama_api_url}'"
        print(error_detail)
        raise HTTPException(
            status_code=500,
            detail="网络连接或本地大模型服务故障 (Ollama 503/Timeout)。请确保 Ollama 应用程序在后台运行且模型已加载。"
        )


# --- TTS 文本转语音接口 (本地静音占位符实现) ---

@app.post("/api/tts", response_model=TTSResponse)
async def generate_tts(request: TTSRequest):
    """
    TTS 接口现在返回一个硬编码的静音 WAV 文件 Base64 字符串。
    """
    print(f"TTS Request received, returning silent placeholder for text: {request.text[:50]}...")
    await asyncio.sleep(0.1)
    return TTSResponse(audio_base64=SILENT_WAV_BASE64)


# 2. 根路由返回 index.html (SPA 入口)
@app.get("/{full_path:path}")
async def serve_index(full_path: str):
    """
    Catch-all 路由，将所有未匹配的路由指向 index.html，
    以支持 Vue 的客户端路由 (History Mode)。
    """
    index_file_path = os.path.join(STATIC_DIR, "index.html")

    if not os.path.exists(index_file_path):
        print(f"FATAL ERROR: index.html not found at path: {index_file_path}")
        return {"message": "Frontend not built or index.html not found inside the executable package."}

    return FileResponse(index_file_path)
