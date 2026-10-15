# 年报分析工作台

基于原有年报分析 Python 项目，按 websitefee.md 的方案 A 改为 Streamlit 一体化应用。

## 本地运行

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Streamlit Community Cloud 发布

1. 将本目录内容放入你控制的 GitHub 仓库。不要提交 `.deps`、密钥、缓存或虚拟环境。
2. 登录 https://share.streamlit.io/ ，选择 Create app，指定该仓库和分支。
3. 主文件选择 `app.py`（如果本目录作为仓库子目录上传，则填写相应子路径）。
4. Advanced settings 中选择 Python 3.12，随后 Deploy。
5. 在部署日志确认 requirements.txt 安装成功，再验证已有报告、在线取数和报告下载。

使用平台提供的免费二级域名，不添加付费资源。实际免费资格、额度、休眠与用途限制以发布时官方界面为准：
https://docs.streamlit.io/deploy/streamlit-community-cloud

## 运行说明

- `examples/` 是原项目的历史输出，页面明确标注未重新核验。
- 在线分析使用临时独立目录，完成后保留结果在当前会话供下载，不承诺云端持久化。
- 为保护 PDF 解析器的共享缓存，同一进程仅运行一个分析任务。
- 季报仅分析结构化数据；半年报和年报可启用 PDF 附注解析。
- 数据源可能限制云服务器访问；发布后必须实测，失败时仍可浏览已有报告。
- 已修复所选报告期缺失时回退其他年份的问题。

## 部署状态

当前目录是部署准备文件，不代表已经上线；成功部署后应记录平台返回的真实网站地址。
