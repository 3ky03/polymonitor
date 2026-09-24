# polyData Updates

这个文件用来记录对外可见的每日进展，适合放“今天上了什么新功能、改了什么体验、下一步准备做什么”。

使用建议：

- `README.md` 只保留最近 `5-10` 条高可见更新。
- 这里按日期倒序追加，作为完整更新线。
- 私有排障、部署流水、临时草稿继续放 `document/`。
- 如果某项工作还没做完，写成“推进中”比写成“已完成”更稳妥。

---

## 2026-04-16

### ◆ Shipped

- 建立公开更新入口：`README.md` 新增 `Recent Updates`
- 新增每日更新档案页：`docs/updates.md`
- 补上一个适合持续复制使用的更新模板

### ◇ In Progress

- 整理本机 `systemd` 化和远端前端部署路线

### → Next

- 增加 GitHub Actions 自动部署 `webpage/dist`
- 整理 `deploy/nginx/` 的可复用配置模板

### ! Notes

- `README` 只展示最近更新，完整历史沉淀在本页

---

## Template

以后每天可以直接复制下面这段：

```md
## YYYY-MM-DD

### ◆ Shipped

- 
- 

### ◇ Improved

- 
- 

### ◇ In Progress

- 

### → Next

- 
- 

### ! Notes

- 
```

如果当天想写得更像产品更新，也可以用这个轻松一点的版本：

```md
## YYYY-MM-DD

`[Launch]`
- 

`[Polish]`
- 

`[Fix]`
- 

`[Next Up]`
- 
```
