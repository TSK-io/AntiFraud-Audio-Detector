import json
import math
import os
import threading
import traceback
from pathlib import Path

import gradio as gr
import librosa
import numpy as np
import torch
from transformers import AutoProcessor, Qwen2AudioForConditionalGeneration
from audio_guard import (
    UI_DEFAULT_CHAT_TEXT,
    UI_DEFAULT_TRANSCRIPT,
    analyze_chat_text,
    build_detection_prompt,
    make_error_result,
    normalize_guard_result,
)

try:
    import spaces
except Exception:
    class _SpacesFallback:
        @staticmethod
        def GPU(func=None, **_kwargs):
            if func is None:
                return lambda wrapped: wrapped
            return func

    spaces = _SpacesFallback()


MODEL_ID = "JimmyMa99/AntiFraud-SFT"
SAMPLE_RATE = 16000


def read_env_int(name, default, minimum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def read_env_float(name, default, minimum):
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(minimum, value)


MAX_AUDIO_SECONDS = read_env_int("AUDIO_GUARD_MAX_SECONDS", 180, 1)
MAX_AUDIO_MB = read_env_int("AUDIO_GUARD_MAX_MB", 80, 1)
MIN_AUDIO_SECONDS = read_env_float("AUDIO_GUARD_MIN_SECONDS", 0.2, 0.0)
MAX_AUDIO_BYTES = MAX_AUDIO_MB * 1024 * 1024
MAX_AUDIO_SAMPLES = MAX_AUDIO_SECONDS * SAMPLE_RATE

processor = None
model = None
_model_lock = threading.Lock()
_model_load_error = None


def load_model_components():
    global processor, model, _model_load_error

    if processor is not None and model is not None:
        return processor, model

    with _model_lock:
        if processor is not None and model is not None:
            return processor, model

        try:
            print("正在加载 Processor...", flush=True)
            loaded_processor = AutoProcessor.from_pretrained(MODEL_ID)

            print("正在加载 Model (使用 bfloat16 以节省显存)...", flush=True)
            loaded_model = Qwen2AudioForConditionalGeneration.from_pretrained(
                MODEL_ID,
                torch_dtype=torch.bfloat16,
                device_map="auto",
            )
            loaded_model.eval()

            processor = loaded_processor
            model = loaded_model
            _model_load_error = None
            print("模型加载完成！", flush=True)
            return processor, model
        except Exception as exc:
            _model_load_error = traceback.format_exc()
            print(f"[startup] model_load_error={_model_load_error}", flush=True)
            raise RuntimeError(f"模型加载失败：{exc}") from exc


def load_audio_array(audio_path):
    path = Path(audio_path)
    if not path.exists() or not path.is_file():
        raise ValueError("音频文件不存在或不是普通文件，请重新上传。")

    size = path.stat().st_size
    if size <= 0:
        raise ValueError("音频文件为空，请重新上传。")
    if size > MAX_AUDIO_BYTES:
        raise ValueError(f"音频文件过大（>{MAX_AUDIO_MB}MB），请裁剪后再提交。")

    try:
        duration = librosa.get_duration(path=str(path))
    except Exception:
        duration = None
    if duration is not None and duration < MIN_AUDIO_SECONDS:
        raise ValueError("音频时长太短，请上传包含有效通话内容的录音。")
    if duration is not None and duration > MAX_AUDIO_SECONDS:
        raise ValueError(f"音频超过 {MAX_AUDIO_SECONDS} 秒，请裁剪后再提交。")

    try:
        audio_array, _ = librosa.load(
            str(path),
            sr=SAMPLE_RATE,
            mono=True,
            duration=MAX_AUDIO_SECONDS + 1,
        )
    except Exception as exc:
        raise ValueError(f"音频解码失败，请确认文件格式可播放：{exc}") from exc

    if audio_array is None or len(audio_array) == 0:
        raise ValueError("没有读取到有效音频数据，请重新上传。")
    if len(audio_array) < int(MIN_AUDIO_SECONDS * SAMPLE_RATE):
        raise ValueError("音频时长太短，请上传包含有效通话内容的录音。")
    if len(audio_array) > MAX_AUDIO_SAMPLES:
        raise ValueError(f"音频超过 {MAX_AUDIO_SECONDS} 秒，请裁剪后再提交。")

    if not np.isfinite(audio_array).all():
        audio_array = np.nan_to_num(audio_array, nan=0.0, posinf=0.0, neginf=0.0)

    return audio_array.astype(np.float32, copy=False)


def get_model_input_device(loaded_model):
    if torch.cuda.is_available():
        return torch.device("cuda")

    try:
        return next(parameter.device for parameter in loaded_model.parameters() if parameter.device.type != "meta")
    except StopIteration:
        return getattr(loaded_model, "device", torch.device("cpu"))
    except Exception:
        return getattr(loaded_model, "device", torch.device("cpu"))


if os.getenv("AUDIO_GUARD_EAGER_LOAD", "1") == "1":
    try:
        load_model_components()
    except Exception:
        pass

# 2. 定义推理函数并加上 @spaces.GPU 装饰器
# Zero GPU 只有在执行带有此装饰器的函数时，才会真正分配物理显卡
@spaces.GPU
def process_audio(audio_path, transcript):
    resolved_audio_path = resolve_audio_path(audio_path)
    if not resolved_audio_path:
        return make_error_result(
            f"后端没有收到有效音频文件路径，请重新上传或录制后再提交。收到的输入类型：{type(audio_path).__name__}。"
        )

    try:
        # 使用 librosa 读取音频并重采样到 16000Hz (Qwen2-Audio的标准采样率)
        audio_array = load_audio_array(resolved_audio_path)
        loaded_processor, loaded_model = load_model_components()
        guard_prompt = build_detection_prompt(transcript)
        print(
            "[analyze] received "
            f"audio={Path(resolved_audio_path).name} "
            f"seconds={len(audio_array) / SAMPLE_RATE:.2f} "
            f"transcript_chars={len((transcript or '').strip())}",
            flush=True,
        )
        
        # 构造符合 Qwen2-Audio 要求的对话模板
        messages = [
            {"role": "system", "content": "你是严谨的中文通话反诈证据筛查器，只输出 JSON 对象。"},
            {"role": "user", "content": [
                {"type": "audio", "audio_url": "dummy_path"}, # processor 需要一个占位符来生成 <|AUDIO|> token
                {"type": "text", "text": guard_prompt}
            ]}
        ]
        
        # 应用聊天模板
        text = loaded_processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        
        # 处理输入数据，转换为 tensor
        inputs = loaded_processor(
            text=text, 
            audio=audio_array,
            sampling_rate=SAMPLE_RATE,
            return_tensors="pt", 
            padding=True
        )
        if "input_features" not in inputs:
            return make_error_result("模型处理器没有生成音频特征；本次没有进行音频判断。")
        feature_frames = int(inputs.get("feature_attention_mask", torch.tensor([])).sum().item())
        print(
            "[analyze] encoded_audio "
            f"input_features_shape={tuple(inputs['input_features'].shape)} "
            f"feature_frames={feature_frames}",
            flush=True,
        )
        
        # Zero GPU 会在函数执行时分配 CUDA；本地调试时退回到模型所在设备。
        target_device = get_model_input_device(loaded_model)
        inputs = {k: v.to(target_device) for k, v in inputs.items()}
        
        # 生成回答
        with torch.no_grad():
            generated_ids = loaded_model.generate(
                **inputs, 
                max_new_tokens=512,
                do_sample=False,
                repetition_penalty=1.05
            )
            
        # 截取新生成的部分并解码
        generated_ids = generated_ids[:, inputs["input_ids"].shape[1]:]
        response = loaded_processor.batch_decode(
            generated_ids, 
            skip_special_tokens=True, 
            clean_up_tokenization_spaces=False
        )[0]
        print(f"[analyze] raw_model_output={response}", flush=True)

        result = normalize_guard_result(response, evidence_context=transcript)
        print(
            "[analyze] normalized_output="
            f"{json.dumps(result, ensure_ascii=False)}",
            flush=True,
        )

        return result

    except ValueError as e:
        print(f"[analyze] input_error={e}", flush=True)
        return make_error_result(str(e))

    except Exception as e:
        print(f"[analyze] error={traceback.format_exc()}", flush=True)
        return make_error_result(f"处理过程中发生错误：{str(e)}")


def resolve_audio_path(audio_input):
    if audio_input is None:
        return None

    if isinstance(audio_input, (str, Path)):
        path = str(audio_input)
        return path if Path(path).exists() and Path(path).is_file() else None

    if isinstance(audio_input, dict):
        for key in ("path", "name", "file", "orig_name"):
            value = audio_input.get(key)
            if isinstance(value, (str, Path)) and Path(value).exists() and Path(value).is_file():
                return str(value)

    if hasattr(audio_input, "path"):
        path = getattr(audio_input, "path")
        if isinstance(path, (str, Path)) and Path(path).exists() and Path(path).is_file():
            return str(path)

    return None


def process_chat(chat_input):
    try:
        result = analyze_chat_text(chat_input)
        print(
            "[chat_analyze] normalized_output="
            f"{json.dumps(result, ensure_ascii=False)}",
            flush=True,
        )
        return result
    except Exception as e:
        print(f"[chat_analyze] error={traceback.format_exc()}", flush=True)
        return make_error_result(f"处理聊天文本时发生错误：{str(e)}")


# 3. 构建 Gradio 界面
with gr.Blocks(title="AntiFraud-SFT 反诈检测") as demo:
    gr.Markdown("# 🛡️ AntiFraud-SFT 反诈检测")
    gr.Markdown(
        "基于 **Qwen2-Audio-7B** 微调的防诈骗检测模型，并提供聊天文本规则识别。\n\n"
        "**说明**: 本环境使用 Hugging Face Zero GPU。模型启动约需1-2分钟，点击 Submit 后系统会动态分配显卡进行计算。\n\n"
        "**外部 API**: 语音接口 `/analyze`，聊天文本接口 `/chat_analyze`。可在页面底部 **Use via API** 查看调用示例。"
    )

    with gr.Tabs():
        with gr.Tab("语音检测"):
            with gr.Row():
                with gr.Column():
                    audio_input = gr.Audio(
                        sources=["upload", "microphone"],
                        type="filepath",
                        label="上传或录制待检测语音",
                        editable=False,
                    )
                    text_input = gr.Textbox(
                        label="已知转写（可选，不填也可以）",
                        value=UI_DEFAULT_TRANSCRIPT,
                        placeholder="如果已有 ASR/人工转写，可以粘贴在这里；没有就留空。",
                        lines=3
                    )
                    submit_btn = gr.Button("开始分析 (Submit)", variant="primary")

                with gr.Column():
                    output_text = gr.JSON(label="结构化分析结果 (Guard JSON)")

            submit_btn.click(
                fn=process_audio,
                inputs=[audio_input, text_input],
                outputs=output_text,
                api_name="analyze"
            )

        with gr.Tab("聊天文本检测"):
            with gr.Row():
                with gr.Column():
                    chat_input = gr.Textbox(
                        label="聊天记录或 messages JSON",
                        value=UI_DEFAULT_CHAT_TEXT,
                        placeholder=(
                            "支持直接粘贴聊天记录，也支持 JSON："
                            "{\"messages\":[{\"sender\":\"客服\",\"content\":\"请把验证码发给我\"}]}"
                        ),
                        lines=12,
                    )
                    chat_submit_btn = gr.Button("分析聊天文本", variant="primary")

                with gr.Column():
                    chat_output = gr.JSON(label="聊天文本分析结果 (Guard JSON)")

            chat_submit_btn.click(
                fn=process_chat,
                inputs=chat_input,
                outputs=chat_output,
                api_name="chat_analyze"
            )

# 启动服务，关闭 SSR 以避免 asyncio 报错
if __name__ == "__main__":
    demo.queue().launch(ssr_mode=False)
