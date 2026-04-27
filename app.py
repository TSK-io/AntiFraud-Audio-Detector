import gradio as gr
import spaces
import torch
import librosa
from transformers import AutoProcessor, Qwen2AudioForConditionalGeneration

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
def process_audio(audio_path, text_prompt):
    if not audio_path:
        return "⚠️ 请先上传或录制一段音频文件。"
    if not text_prompt:
        text_prompt = "请分析这段录音内容，判断是否存在电信诈骗风险并说明理由。"

    try:
        # 使用 librosa 读取音频并重采样到 16000Hz (Qwen2-Audio的标准采样率)
        audio_array, sr = librosa.load(audio_path, sr=16000)
        
        # 构造符合 Qwen2-Audio 要求的对话模板
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": [
                {"type": "audio", "audio_url": "dummy_path"}, # processor 需要一个占位符来生成 <|AUDIO|> token
                {"type": "text", "text": text_prompt}
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
        
        # 将输入数据明确移动到 GPU
        inputs = {k: v.to("cuda") for k, v in inputs.items()}
        
        # 生成回答
        with torch.no_grad():
            generated_ids = model.generate(
                **inputs, 
                max_new_tokens=1024,
                temperature=0.7,
                top_p=0.5,
                repetition_penalty=1.1
            )
            
        # 截取新生成的部分并解码
        generated_ids = generated_ids[:, inputs["input_ids"].shape[1]:]
        response = processor.batch_decode(
            generated_ids, 
            skip_special_tokens=True, 
            clean_up_tokenization_spaces=False
        )[0]
        
        return response

    except Exception as e:
        return f"处理过程中发生错误: {str(e)}"

# 3. 构建 Gradio 界面
with gr.Blocks(title="AntiFraud-SFT 电信诈骗音频检测") as demo:
    gr.Markdown("# 🛡️ AntiFraud-SFT 电信诈骗音频慢思考检测模型")
    gr.Markdown(
        "基于 **Qwen2-Audio-7B** 微调的防诈骗检测模型。\n\n"
        "**说明**: 本环境使用 Hugging Face Zero GPU。模型启动约需1-2分钟，点击 Submit 后系统会动态分配显卡进行计算。"
    )
    
    with gr.Row():
        with gr.Column():
            audio_input = gr.Audio(type="filepath", label="上传或录制疑似诈骗语音")
            text_input = gr.Textbox(
                label="附加提示词 (Prompt)", 
                value="请仔细听这段对话，判断这是不是电信诈骗？如果是，请指出诈骗分子的套路并进行慢思考分析。",
                lines=3
            )
            submit_btn = gr.Button("开始分析 (Submit)", variant="primary")
            
        with gr.Column():
            output_text = gr.Textbox(label="模型分析结果 (Analysis Result)", lines=12)
            
    submit_btn.click(
        fn=process_audio,
        inputs=[audio_input, text_input],
        outputs=output_text
    )

# 启动服务，关闭 SSR 以避免 asyncio 报错
demo.launch(ssr_mode=False)