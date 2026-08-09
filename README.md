# astrbot_soutu_download

AstrBot 插件，提供两个 LLM Tool：

- `soutubot_search_tool`：用户发送或引用图片，并明确要求搜索这张图片时，调用 soutubot.moe 反向搜图。
- `nhentai_download_tool`：用户明确要求下载 nhentai 某个编号时，下载并发送生成的 PDF。

插件内的 `download_comic_impl.py` 和 `soutubot_search_impl.py` 是原 CLI 文件的副本；原始文件不会被修改。

## 安装

将整个目录复制到 AstrBot 的 `data/plugins/` 目录，或在 AstrBot 插件管理中加载该目录。安装依赖：

```text
pip install -r requirements.txt
```

如果 nhentai 实例要求认证，可继续使用原 CLI 支持的环境变量：

- `NHENTAI_API_KEY`
- `NHENTAI_ACCESS_TOKEN`
- `NHENTAI_COOKIE`

可选插件环境变量：

- `ASTRBOT_SOUTU_TIMEOUT`：单次网络请求超时时间，默认 `60` 秒。
- `ASTRBOT_SOUTU_RESULT_LIMIT`：搜图结果展示条数，默认 `10`。
