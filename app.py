import gradio as gr
import spaces
import torch
import librosa
from transformers import AutoProcessor, Qwen2AudioForConditionalGeneration
from audio_guard import UI_DEFAULT_TRANSCRIPT, build_detection_prompt, make_error_result, normalize_guard_result

# 1. 全局加载模型和处理器 (Zero GPU 会先将模型加载到 CPU 内存，推理时动态移至 GPU)
MODEL_ID = "JimmyMa99/AntiFraud-SFT"

print("正在加载 Processor...")
processor = AutoProcessor.from_pretrained(MODEL_ID)

print("正在加载 Model (使用 bfloat16 以节省显存)...")
model = Qwen2AudioForConditionalGeneration.from_pretrained(
    MODEL_ID,
    torch_dtype=torch.bfloat16,
    device_map="auto" 
)
print("模型加载完成！")

# 2. 定义推理函数并加上 @spaces.GPU 装饰器
# Zero GPU 只有在执行带有此装饰器的函数时，才会真正分配物理显卡
@spaces.GPU
def process_audio(audio_path, transcript):
    if not audio_path:
        return make_error_result("请先上传或录制一段音频文件。")

    try:
        # 使用 librosa 读取音频并重采样到 16000Hz (Qwen2-Audio的标准采样率)
        audio_array, _ = librosa.load(audio_path, sr=16000)
        guard_prompt = build_detection_prompt(transcript)
        
        # 构造符合 Qwen2-Audio 要求的对话模板
        messages = [
            {"role": "system", "content": "你是严谨的中文通话反诈证据筛查器，只输出 JSON 对象。"},
            {"role": "user", "content": [
                {"type": "audio", "audio_url": "dummy_path"}, # processor 需要一个占位符来生成 <|AUDIO|> token
                {"type": "text", "text": guard_prompt}
            ]}
        ]
        
        # 应用聊天模板
        text = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        
        # 处理输入数据，转换为 tensor
        inputs = processor(
            text=text, 
            audios=audio_array, 
            return_tensors="pt", 
            padding=True
        )
        
        # Zero GPU 会在函数执行时分配 CUDA；本地调试时退回到模型所在设备。
        target_device = "cuda" if torch.cuda.is_available() else model.device
        inputs = {k: v.to(target_device) for k, v in inputs.items()}
        
        # 生成回答
        with torch.no_grad():
            generated_ids = model.generate(
                **inputs, 
                max_new_tokens=512,
                do_sample=False,
                repetition_penalty=1.05
            )
            
        # 截取新生成的部分并解码
        generated_ids = generated_ids[:, inputs["input_ids"].shape[1]:]
        response = processor.batch_decode(
            generated_ids, 
            skip_special_tokens=True, 
            clean_up_tokenization_spaces=False
        )[0]
        
        return normalize_guard_result(response, evidence_context=transcript)

    except Exception as e:
        return make_error_result(f"处理过程中发生错误：{str(e)}")

# 3. 构建 Gradio 界面
with gr.Blocks(title="AntiFraud-SFT 电信诈骗音频检测") as demo:
    gr.Markdown("# 🛡️ AntiFraud-SFT 电信诈骗音频慢思考检测模型")
    gr.Markdown(
        "基于 **Qwen2-Audio-7B** 微调的防诈骗检测模型。\n\n"
        "**说明**: 本环境使用 Hugging Face Zero GPU。模型启动约需1-2分钟，点击 Submit 后系统会动态分配显卡进行计算。\n\n"
        "**外部 API**: 本 Space 已暴露 `/analyze` 接口，可在页面底部 **Use via API** 查看调用示例。"
    )
    
    with gr.Row():
        with gr.Column():
            audio_input = gr.Audio(type="filepath", label="上传或录制待检测语音")
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

# 启动服务，关闭 SSR 以避免 asyncio 报错
demo.queue().launch(ssr_mode=False)
