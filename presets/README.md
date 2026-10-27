# 仓库预设

本目录是内置资源的来源，随镜像打包，启动时离线同步至 SQLite。页面不能直接改写这些文件；用户通过“复制并编辑”创建独立副本。

## 清单

`catalog.json` 的 `resources` 中，每项包含稳定的 `id`、`kind`（`templates` 或 `skills`）、相对 `path` 和 `source`。Skill 另有库内中文 `title`、`tags`、`target_models`、`language`，这些展示字段不写回原始 SKILL.md。

稳定 ID 不随名称修改而改变。内容未变不新增版本，更新只改变对应内置项。移除清单项会归档内置资源，保留历史及已有副本。

## 模板

每个目录至少含 `manifest.json` 和 `template.md`。示例：

```json
{
  "title": "写实提示词模板",
  "description": "填写主体后直接组合提示词",
  "template_kind": "prompt",
  "tags": ["写实", "生图"],
  "target_models": ["Krea 2"],
  "language": "zh",
  "variables": [
    {"name": "subject", "label": "主体", "type": "textarea", "required": true}
  ],
  "context_paths": []
}
```

正文写 `写实摄影，{{subject}}，自然光，真实材质。`。变量类型支持 `text`、`textarea`、`select`、`number`，可设置说明、默认值、必填和数字范围；下拉需要 `options`。只做字面替换，不运行表达式或循环，输入中的占位符不会再次展开。

`template_kind=assistant` 用于给外部 AI 的助手指令，`prompt` 用于直接使用的成品提示词。`context_paths` 指向包内 UTF-8 参考文件；渲染时附加其全文，复制为自定义资源后仍使用冻结的文件内容。MiniMax 助手的 `context/ref-en.txt` 与固定版本的官方规范相同。

## Skill

每个 Skill 保留完整原始目录，至少有 `SKILL.md`。`name` 使用小写字母、数字及连字符，`description` 不能为空；其他 frontmatter 和正文不重写。可包含中英文入口、`references/`、`scripts/`、`assets/`、`agents/` 及其他附属文件。

源码目录 `skills/minimax-h3/` 来自 MiniMax-AI/MiniMax-H3，固定提交为 `d21241f0a4b3acbb34c97dae47fa417b7065e438`。`minimax-source.json` 记录所有原始文件的 SHA-256 和来源；上游当前未附许可文件，清单中的许可字段据此保留为空，不臆造授权声明。

`python3 scripts/vendor_minimax.py` 可重新获取该固定版本并校验 Git blob 哈希。它是维护命令，普通启动不会联网。更换上游版本时，需一起更新清单、校验记录和助手所冻结的参考文件，然后运行测试审查变更。

`skills/original/creative-director/` 为本项目原创的通用导演 Skill，不依赖 MiniMax Hub。预设是可复用指令与方法资料，没有声称已执行模型或实测生成质量。

## 验证

```bash
.venv/bin/python -m pytest backend/tests/test_resources.py -q
```

测试校验预设数量、原始文件哈希、完整导出、标签隔离、历史恢复、更新不覆盖副本及重复启动幂等性。目录内不放模型权重、凭证或本地缓存。
