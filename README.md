# ACEMusic Skill（ACE-Step 免费文生音乐）

免费调用 [ACEMusic](https://acemusic.ai) 云端 API（ACE-Step v1.5 turbo，OpenAI 兼容接口）生成音乐/BGM 的 Agent skill。零依赖 Python CLI（标准库），带 504 网关超时自动重试 + 场景表批量生成，专为**漫剧/短剧配乐**场景设计。

## 能力

| 命令 | 说明 |
|---|---|
| `python scripts/acemusic.py models` | 查看可用模型与免费额度（pricing 全 0） |
| `python scripts/acemusic.py gen "15秒 动漫风BGM，国风电子，120BPM" --name opener` | 单条生成 → MP3 |
| `python scripts/acemusic.py batch assets/scenes.example.json --out out/` | 按场景表批量生成一集漫剧的全套 BGM |

- 输入模态：text + audio（可喂参考音频）；输出：MP3（base64 data URL）+ 文字 caption
- 免费：ACEMusic 官方云，pricing 全 0
- 限制：同步请求 60s 网关超时（长曲/人声曲常 504）→ CLI 内置 429/5xx 线性退避重试；建议片段 ≤15s

## 安装（作为 Agent skill）

1. 把本目录（`SKILL.md` + `scripts/` + `assets/` + `references/`）复制到你的 agent skill 目录
2. 获取免费 key：<https://acemusic.ai> → 存入环境变量 `ACEMUSIC_API_KEY`（或本地 `.acemusic_key` 文件，已被 .gitignore 排除）
3. 需要 Python 3.8+，无第三方依赖

```bash
# 示例
$env:ACEMUSIC_API_KEY = "你的key"          # Windows
export ACEMUSIC_API_KEY="你的key"          # Linux/macOS
python scripts/acemusic.py gen "10秒 转场lofi，钢琴，100BPM"
```

## 漫剧工作流（示例）

```
分镜脚本 → 场景表 JSON（assets/scenes.example.json 格式）
        → batch 批量生成 BGM（自动重试 504）
        → ffmpeg 拼接/循环 → 合成进成片
角色对白/旁白 → 用 TTS（EdgeTTS / CosyVoice / 讯飞等，不在本 skill 范围）
```

## 接口细节

见 [references/api-notes.md](references/api-notes.md)（端点、鉴权、响应结构、60s 超时、法律提示，均 2026-10 实测）。

## License

MIT（本仓库代码）。生成的音乐版权/合规遵循 ACE-Step 项目条款（见上游仓库 LICENSE 与免责声明：需自查原创性、标注 AI 生成、风格模仿需授权）。
