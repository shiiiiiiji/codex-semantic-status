<p align="center"><img src="docs/assets/overview.svg" alt="Codex 会话的语义任务状态" width="920"></p>

# Codex Semantic Status

[English](README.md) · [架构](docs/design.md) · [配置与排障](docs/configuration.md)

**给会话标记任务进展。** 插件根据会话里的目标、实际产出和剩余事项，异步更新标题开头的状态 emoji，并用持久化预算控制模型调用。

例如，代码已写好但未测试，标记为 🧪；等待用户决定，标记为 ❓；用户要求合入代码而审查尚未通过，标记为 ⏳。回复结束本身不能作为 ✅ 的依据。

## 安装并信任 Hook

需要 Python 3.11 或更新版本、已登录的 Codex，以及 macOS 或 Linux。Python 运行时无第三方依赖，不需要 npm。

```sh
git clone https://github.com/shiiiiiiji/codex-semantic-status.git
cd codex-semantic-status
python3 scripts/semantic_status.py doctor
python3 scripts/semantic_status.py install
```

安装命令登记当前 checkout 为本地插件来源，并使用 Codex 的官方安装接口生成缓存副本。安装不会自动信任 Hook。

在 Codex CLI 中输入 `/hooks`，找到 `codex-semantic-status@semantic-status-local` 的 Stop Hook，查看命令并信任这条定义。新建或重新打开的会话会加载已启用的插件。保留本地 checkout，供后续安装和更新使用。

如果 checkout 路径发生变化，使用 `install --replace-source` 明确替换这个插件的旧来源。更新运行文件时需要提升插件版本再安装；Hook 定义变化后需要再次审阅。

## 后台执行与状态判断

Stop Hook 设置为 `async: true`，只向本地队列投递事件。独立后台进程合并重复事件、读取会话、调用轻量模型，再写回标题。

主会话不等待分类，也不会收到后台生成的消息或附加上下文。后台不会恢复或中断原会话。模型在不持久化的临时会话中分类，工具、插件和外部集成关闭。

| 标识 | 任务状态 |
| --- | --- |
| 📥 | 待处理，尚未开始 |
| ▶️ | 工作尚未结束，可以继续推进 |
| ❓ | 等用户决定或确认 |
| ⏳ | 等外部回复或结果 |
| ⛔ | 有障碍，需要先解决 |
| 🧪 | 已有产出，仍需验证或验收 |
| ✅ | 用户要求的目标已有完成证据 |
| ⏸️ | 用户明确暂缓 |

置信度不足时保留原标题。已有 🚩 等自定义 emoji 时默认保留。手动改名后，插件暂停这个会话的自动更新。

## 成本控制

默认合并 60 秒内的事件，每个会话两次推理至少间隔 300 秒；每日最多尝试 20 次，每个会话最多 6 次。全局每日 Token 准入预算为 50,000，每次预留 20,000，按 Asia/Shanghai 的日期结算。

框架和全局 AGENTS.md 也会消耗 Token。在一个已验证的桌面运行时中，三个简单模拟场景每次约消耗 11,600 个总 Token。默认预算在类似用量下通常每天准入约 2–3 次判断；这个样本不代表所有会话的费用或准确率。

预算限制新请求的准入，不是精确金额上限。单笔在途请求可能超出预留或日预算。已知用量在失败后仍计入；未知用量保留预留额度。模型费用使用当前 Codex 账号或模型服务。

模型输入最多 6,000 字符，优先保留最近的用户要求；要求被截断时拒绝标记 ✅。自动选模依次寻找模型列表中的 luna、nano 或 mini，名称不保证价格，应按实际服务费用选型。

```sh
python3 scripts/semantic_status.py status
python3 scripts/semantic_status.py logs --limit 20
python3 scripts/semantic_status.py configure --set 'max_tokens_per_day=50000'
python3 scripts/semantic_status.py configure --set 'enabled=false'
```

## 管理指定会话

把下面的 `THREAD_ID` 替换为 Codex 会话 ID。

```sh
python3 scripts/semantic_status.py pause THREAD_ID
python3 scripts/semantic_status.py resume THREAD_ID
python3 scripts/semantic_status.py enqueue THREAD_ID
python3 scripts/semantic_status.py preview THREAD_ID
```

preview 消耗正常预算，但只展示建议标题，不写回。enqueue 使用同样的事件合并与预算规则。resume 解除人工改名后的暂停；后续内容变化并触发新事件时可重新判断。

## 使用限制

插件把一部分会话文本发送给当前模型服务，运行数据保存在源码目录之外；详见 [隐私说明](SECURITY.md)。日志只保存结果码与错误类型，公开问题时仍应隐去会话 ID。

语义状态只反映已有会话证据，不能独立证明外部测试、代码合并、部署或现实事务已经完成。默认上下文覆盖原始目标、最近三轮和之前的摘要，较早的修正仍可能遗漏。

标题接口没有原子比较后写入能力，手动改名仍有极短的竞争窗口。桌面标题可能要重新打开会话后刷新。app-server 接口属于实验能力，不同版本可能有差异；当前不支持 Windows。

## 开发与贡献

```sh
python3 -B -m unittest discover -s tests -v
python3 -B scripts/package_release.py
```

测试完全离线，不要求 Codex 登录或云端模型。开源项目包含 macOS/Linux、Python 3.11–3.14 的 CI 配置和版本标签发布流程。采用 MIT 许可证；贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。
