<script setup>
import { ref, watch, nextTick } from 'vue';

// --- 全局配置 ---
const CHAT_API_URL = 'http://127.0.0.1:8000/api/chat';
const TTS_API_URL = 'http://127.0.0.1:8000/api/tts';
const MODEL_NAME = 'ai-girlfriend'; // 本地 Ollama 模型名称

// --- 状态管理 ---
const messages = ref([]);
const prompt = ref('');
const isSending = ref(false);
const audioPlayer = ref(null);
const audioDataUrl = ref(null);
const voice = ref('Puck'); // TTS 语音名称
const isPlaying = ref(false);
const currentAudioId = ref(null);
const lastError = ref('');

// --- 本地持久化 (可选，用于保持聊天记录) ---
// const loadMessages = () => {
//     const saved = localStorage.getItem('chatMessages');
//     messages.value = saved ? JSON.parse(saved) : [];
// };
// const saveMessages = () => {
//     localStorage.setItem('chatMessages', JSON.stringify(messages.value));
// };
// loadMessages();

// --- 监听消息变化，滚动到底部 ---
watch(messages, () => {
    nextTick(() => {
        const container = document.querySelector('.chat-messages');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    });
}, { deep: true });

// --- 工具函数 ---

/**
 * 带有指数退避策略的 fetch 请求
 * @param {string} url - API URL
 * @param {object} options - fetch options
 * @param {number} maxRetries - 最大重试次数
 * @param {number} baseDelay - 基础延迟时间 (ms)
 */
async function exponentialBackoffFetch(url, options = {}, maxRetries = 3, baseDelay = 300) {
    for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
            const response = await fetch(url, options);

            if (response.ok) {
                return response;
            }

            // 如果是 4xx 或 5xx 错误，记录错误信息并尝试重试
            const errorText = await response.text();
            console.error(`[Backend API] Received ${response.status}. Error: ${errorText}`);

            if (attempt < maxRetries - 1) {
                const delay = baseDelay * Math.pow(2, attempt) + Math.random() * baseDelay;
                console.warn(`[Backend API] Received ${response.status}. Retrying in ${delay}ms... (Attempt ${attempt + 1}/${maxRetries})`);
                await new Promise(resolve => setTimeout(resolve, delay));
            } else {
                throw new Error(`HTTP Error ${response.status}: Internal Server Error`);
            }
        } catch (error) {
            console.error(error);
            if (attempt < maxRetries - 1) {
                const delay = baseDelay * Math.pow(2, attempt) + Math.random() * baseDelay;
                console.warn(`[Connection] Error connecting. Retrying in ${delay}ms... (Attempt ${attempt + 1}/${maxRetries})`);
                await new Promise(resolve => setTimeout(resolve, delay));
            } else {
                throw error; // 最终抛出错误
            }
        }
    }
}

/**
 * 将 PCM 16bit 音频数据转换为 WAV Blob
 * @param {Int16Array} pcm16 - 16位PCM音频数据
 * @param {number} sampleRate - 采样率
 * @returns {Blob} WAV Blob
 */
function pcmToWav(pcm16, sampleRate = 44100) {
    const numChannels = 1;
    const bytesPerSample = 2;
    const blockAlign = numChannels * bytesPerSample;
    const byteRate = sampleRate * blockAlign;

    const buffer = new ArrayBuffer(44 + pcm16.length * bytesPerSample);
    const view = new DataView(buffer);

    // RIFF identifier
    writeString(view, 0, 'RIFF');
    // RIFF chunk length
    view.setUint32(4, 36 + pcm16.length * bytesPerSample, true);
    // RIFF type
    writeString(view, 8, 'WAVE');
    // format chunk identifier
    writeString(view, 12, 'fmt ');
    // format chunk length
    view.setUint32(16, 16, true);
    // sample format (1 for PCM)
    view.setUint16(20, 1, true);
    // number of channels
    view.setUint16(22, numChannels, true);
    // sample rate
    view.setUint32(24, sampleRate, true);
    // byte rate
    view.setUint32(28, byteRate, true);
    // block align
    view.setUint16(32, blockAlign, true);
    // bits per sample
    view.setUint16(34, 16, true);
    // data chunk identifier
    writeString(view, 36, 'data');
    // data chunk length
    view.setUint32(40, pcm16.length * bytesPerSample, true);

    // Write PCM data
    let offset = 44;
    for (let i = 0; i < pcm16.length; i++, offset += 2) {
        view.setInt16(offset, pcm16[i], true);
    }

    return new Blob([view], { type: 'audio/wav' });
}

function writeString(view, offset, string) {
    for (let i = 0; i < string.length; i++) {
        view.setUint8(offset + i, string.charCodeAt(i));
    }
}

function base64ToArrayBuffer(base64) {
    const binaryString = atob(base64);
    const len = binaryString.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
        bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes.buffer;
}

// --- 语音合成和播放 ---

async function generateAndPlayTTS(text) {
    lastError.value = '';
    currentAudioId.value = messages.value[messages.value.length - 1].id;
    isPlaying.value = true;
    
    // 构造 TTS API 请求体
    const ttsPayload = {
        text: text,
        voice_name: voice.value
    };

    try {
        const response = await exponentialBackoffFetch(TTS_API_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(ttsPayload),
        });

        const result = await response.json();
        
        // 检查后端是否返回了 base64 数据
        if (!result || !result.audio_base64) {
             throw new Error("TTS API did not return audio data.");
        }

        const base64Data = result.audio_base64;
        
        // --- 核心诊断日志 ---
        console.log(`[TTS DIAGNOSTIC] Received Base64 Length: ${base64Data.length}`);
        // --- 核心诊断日志 ---

        // 1. 将 Base64 数据转换为 ArrayBuffer
        const pcmData = base64ToArrayBuffer(base64Data);

        // 2. 将 PCM 数据转换为 Int16Array (API 返回的是 signed PCM16)
        const pcm16 = new Int16Array(pcmData);

        // 3. 将 PCM 转换为 WAV Blob (我们继续假设 API 采样率为 44100Hz)
        const sampleRate = 44100;
        const wavBlob = pcmToWav(pcm16, sampleRate);

        // --- 核心诊断日志 ---
        console.log(`[TTS DIAGNOSTIC] Final WAV Blob Size: ${wavBlob.size} bytes`);
        // --- 核心诊断日志 ---
        
        // 4. 创建音频 URL 并播放
        if (audioDataUrl.value) {
            URL.revokeObjectURL(audioDataUrl.value); // 清理旧的 URL
        }
        
        // 停止和重置当前播放，防止冲突
        if (audioPlayer.value) {
            audioPlayer.value.pause();
            audioPlayer.value.currentTime = 0;
            // 确保播放器完成重置后才设置新源
            await nextTick(); 
        }

        audioDataUrl.value = URL.createObjectURL(wavBlob);
        
        // 播放逻辑
        if (audioPlayer.value) {
            audioPlayer.value.onended = () => {
                isPlaying.value = false;
                currentAudioId.value = null;
            };
            
            // 尝试播放
            audioPlayer.value.play()
                .then(() => {
                    console.log("Audio playback successfully initiated.");
                })
                .catch(e => {
                    // 检查是否是 Autoplay 策略阻止的播放错误 (NotAllowedError)
                    if (e.name === 'NotAllowedError') {
                        lastError.value = "浏览器自动播放被阻止。请与AI交互后尝试点击播放按钮。";
                        console.error("Autoplay Policy Blocked Playback:", e);
                    } 
                    // 忽略 AbortError (通常是内部冲突)
                    else if (e.name === 'AbortError') {
                         console.warn("Playback aborted (internal browser event). Ignoring this harmless warning.");
                    }
                    // 其他错误
                    else {
                        console.error("Audio playback failed:", e);
                        lastError.value = "语音播放失败，可能是浏览器限制或音频格式问题。";
                    }

                    isPlaying.value = false;
                    currentAudioId.value = null;
                });
        }

    } catch (error) {
        console.error("TTS generation error:", error);
        lastError.value = `语音合成失败: ${error.message}`;
        isPlaying.value = false;
        currentAudioId.value = null;
    }
}

// --- 核心聊天逻辑 ---

async function sendMessage() {
    // 强制停止任何正在进行的播放，确保新的TTS请求是干净的
    stopAudio(); 
    
    if (!prompt.value.trim() || isSending.value) return;

    const userPrompt = prompt.value.trim();
    const history = messages.value.filter(msg => msg.role !== 'system' && msg.role !== 'thinking').map(msg => ({
        role: msg.role === 'user' ? 'user' : 'assistant',
        content: msg.content
    }));

    // 1. 添加用户消息
    const userMessage = { id: Date.now(), role: 'user', content: userPrompt, audio: false };
    messages.value.push(userMessage);
    prompt.value = '';
    isSending.value = true;
    lastError.value = '';

    // 2. 添加 AI 思考中的占位符
    const assistantMessage = { id: Date.now() + 1, role: 'assistant', content: '...', thinking: true, audio: false };
    messages.value.push(assistantMessage);

    try {
        // 3. 构造请求体
        const payload = {
            prompt: userPrompt,
            history: history,
        };

        // 4. 发送请求到 FastAPI 后端
        const response = await exponentialBackoffFetch(CHAT_API_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });

        // 5. 解析响应
        const result = await response.json();
        
        // 检查后端返回的 JSON 结构，提取 'response' 字段
        const modelResponseText = result.response;
        
        if (!modelResponseText || modelResponseText.length === 0) {
            throw new Error("模型返回了空回复。");
        }
        
        // 6. 更新 AI 消息
        const index = messages.value.findIndex(msg => msg.id === assistantMessage.id);
        if (index !== -1) {
            messages.value[index].content = modelResponseText;
            messages.value[index].thinking = false;
        }

        // 7. 触发 TTS 语音合成
        generateAndPlayTTS(modelResponseText);

        // saveMessages(); // 保存消息记录
    } catch (error) {
        console.error("Error sending message:", error);
        lastError.value = `聊天失败: ${error.message}. 请检查本地 Ollama 服务是否就绪。`;
        
        // 移除或更新占位符消息
        const index = messages.value.findIndex(msg => msg.id === assistantMessage.id);
        if (index !== -1) {
            messages.value.splice(index, 1);
        }
        isSending.value = false;
    } finally {
        isSending.value = false;
    }
}

// --- 播放/暂停控制 ---
function toggleAudio(messageId) {
    const message = messages.value.find(msg => msg.id === messageId);
    if (!message || message.thinking || !audioPlayer.value || !message.content) return;

    if (currentAudioId.value === messageId) {
        if (isPlaying.value) {
            audioPlayer.value.pause();
            isPlaying.value = false;
        } else {
            // 确保用户点击后可以播放
            audioPlayer.value.play().catch(e => {
                 console.error("Playback error:", e);
                 lastError.value = "语音播放失败。";
            });
            isPlaying.value = true;
        }
    } else {
        // 请求播放新消息
        if (isPlaying.value) {
            audioPlayer.value.pause();
        }
        generateAndPlayTTS(message.content);
    }
}

function stopAudio() {
    if (audioPlayer.value) {
        audioPlayer.value.pause();
        audioPlayer.value.currentTime = 0;
        isPlaying.value = false;
        currentAudioId.value = null;
    }
}
</script>

<template>
    <div class="flex flex-col h-screen bg-gray-900 text-gray-100 font-inter">
        
        <!-- 顶部标题 -->
        <header class="p-4 border-b border-pink-700 bg-pink-900/50 shadow-lg flex items-center justify-between">
            <h1 class="text-xl font-bold text-pink-300">
                <span class="text-2xl mr-2">💖</span> Joel (AI-Girlfriend)
            </h1>
            <button 
                @click="stopAudio"
                :class="{'opacity-50 cursor-not-allowed': !isPlaying}"
                :disabled="!isPlaying"
                class="text-pink-300 hover:text-pink-100 transition duration-150 p-2 rounded-full bg-pink-800/50">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8 7a1 1 0 00-1 1v4a1 1 0 001 1h4a1 1 0 001-1V8a1 1 0 00-1-1H8z" clip-rule="evenodd" />
                </svg>
            </button>
        </header>

        <!-- 聊天消息区域 -->
        <div class="chat-messages flex-grow p-4 space-y-4 overflow-y-auto custom-scrollbar">
            <!-- 欢迎消息 (系统提示) -->
            <div class="flex justify-start">
                <div class="max-w-xs sm:max-w-md bg-gray-800 p-4 rounded-lg shadow-md border border-pink-700/50">
                    <p class="text-sm italic text-pink-400">
                        我是你的AI-Girlfriend，很高兴能陪你聊天。告诉我你今天发生了什么，好吗？
                    </p>
                </div>
            </div>

            <!-- 消息列表 -->
            <div v-for="message in messages" :key="message.id" class="flex" :class="message.role === 'user' ? 'justify-end' : 'justify-start'">
                <div 
                    class="max-w-xs sm:max-w-md p-4 rounded-xl shadow-xl transition duration-300"
                    :class="{
                        'bg-pink-600 text-white rounded-br-none': message.role === 'user',
                        'bg-gray-700 text-gray-200 rounded-bl-none': message.role === 'assistant',
                        'animate-pulse bg-gray-800 border border-pink-700': message.thinking
                    }"
                >
                    <div class="flex items-center space-x-3">
                        <div v-if="message.role === 'assistant'" class="w-6 h-6 flex-shrink-0">
                            <span class="text-2xl">💖</span>
                        </div>
                        <p class="whitespace-pre-wrap">{{ message.content }}</p>
                        <div v-if="message.role === 'user'" class="w-6 h-6 flex-shrink-0">
                            <span class="text-2xl">👤</span>
                        </div>
                    </div>
                    
                    <!-- 语音播放按钮 (仅针对 AI 回复) -->
                    <div v-if="message.role === 'assistant' && !message.thinking" class="mt-2 text-right">
                        <button 
                            @click="toggleAudio(message.id)"
                            :disabled="isSending"
                            class="text-xs transition duration-150 p-1 rounded-full"
                            :class="{
                                'text-pink-300 bg-pink-800/70 hover:bg-pink-700': currentAudioId !== message.id || !isPlaying,
                                'bg-pink-500 text-white animate-pulse': currentAudioId === message.id && isPlaying,
                                'cursor-not-allowed opacity-50': isSending
                            }"
                        >
                            <svg v-if="currentAudioId === message.id && isPlaying" xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                                <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zM7 8a1 1 0 011 1v2a1 1 0 11-2 0V9a1 1 0 011-1zm5-1a1 1 0 00-1 1v2a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" />
                            </svg>
                            <svg v-else xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                                <path d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z" />
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- 错误提示 -->
        <div v-if="lastError" class="p-2 bg-red-800 text-red-200 text-sm text-center">
            {{ lastError }}
        </div>

        <!-- 输入区域 -->
        <footer class="p-4 bg-gray-800 border-t border-pink-700 shadow-2xl">
            <form @submit.prevent="sendMessage" class="flex space-x-3">
                <input
                    v-model="prompt"
                    type="text"
                    placeholder="说点什么... (例如：今天心情不好)"
                    :disabled="isSending"
                    class="flex-grow p-3 rounded-full bg-gray-900 border border-gray-700 focus:border-pink-500 focus:ring-1 focus:ring-pink-500 transition duration-200 disabled:opacity-70 text-sm"
                />
                <button
                    type="submit"
                    :disabled="isSending || !prompt.trim()"
                    class="p-3 rounded-full text-white font-bold transition duration-200 shadow-md"
                    :class="{
                        'bg-pink-600 hover:bg-pink-700 active:bg-pink-800': !isSending,
                        'bg-gray-500 cursor-not-allowed': isSending
                    }"
                >
                    <svg v-if="!isSending" xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" viewBox="0 0 20 20" fill="currentColor">
                        <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 00.183.197l.182.181 3.551 1.972a1 1 0 001.213-.122l4.992-4.992a1 1 0 00.122-1.213l-1.972-3.551-.181-.182a1 1 0 00-.197-.183l-14-7zM16.99 10a1 1 0 01-1 1H8.828l4.435-4.435L15.99 8.828V10z" />
                    </svg>
                    <svg v-else class="animate-spin h-6 w-6 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                </button>
            </form>
            <audio ref="audioPlayer" :src="audioDataUrl" class="hidden"></audio>
        </footer>
    </div>
</template>

<style scoped>
.font-inter {
    font-family: 'Inter', sans-serif;
}
.chat-messages {
    /* 模拟滚动条的样式，使其更美观 */
    scrollbar-width: thin; /* Firefox */
    scrollbar-color: #f9a8d4 #1f2937; /* Firefox */
}
.chat-messages::-webkit-scrollbar {
    width: 8px;
    background-color: #1f2937; /* 卷轴背景色 */
}
.chat-messages::-webkit-scrollbar-thumb {
    background-color: #f9a8d4; /* 滑块颜色 */
    border-radius: 4px;
    border: 2px solid #1f2937; /* 滑块周围的边框 */
}
</style>
