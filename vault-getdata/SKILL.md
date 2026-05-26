---
name: vault-getdata
description: 拉取关注标的的当前价格，写入 Obsidian frontmatter。默认只更新「持有」或「重点关注」状态的标的。
user_invocable: true
---

## 执行步骤

当用户调用此 skill 时，立即执行以下命令，无需确认：

```bash
python3 .claude/skills/getdata/price_updater.py
```

### 可选参数

用户可能在 `/getdata` 后附加参数：

- `/getdata --all` → 更新所有标的（不过滤状态）
- `/getdata --dry-run` → 只显示价格，不写入
- `/getdata 文件名.md` → 更新指定文件

将用户附加的参数直接追加到命令末尾。

### 执行后

简要汇报结果：更新了多少个标的，有哪些失败的。不需要逐条列出成功的标的。
