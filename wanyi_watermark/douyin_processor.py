import re
import json
import tempfile
import logging
from pathlib import Path
from typing import Optional, Dict

import requests
from urllib import request as urlrequest
from http import HTTPStatus
import dashscope
# 注意：ffmpeg 仅在 extract_audio() 内按需延迟导入（见该方法），
# 此处不在模块顶层导入，避免“解析/下载”等无需 ffmpeg 的链路（resolver / CLI）
# 在未安装 ffmpeg 的环境下导入失败。
from mcp.server.fastmcp import Context

from .diagnostics import parse_log, short_text

# 请求头，模拟移动端访问
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1'
}

# 默认 API 配置
DEFAULT_MODEL = "paraformer-v2"

logger = logging.getLogger(__name__)


class DouyinProcessor:
    """抖音视频处理器"""

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.temp_dir = Path(tempfile.mkdtemp())
        # 设置阿里云百炼API密钥
        dashscope.api_key = api_key

    def __del__(self):
        """清理临时目录"""
        try:
            import shutil
            if hasattr(self, 'temp_dir') and self.temp_dir.exists():
                shutil.rmtree(self.temp_dir, ignore_errors=True)
        except (ImportError, AttributeError):
            # Python 关闭时可能无法导入模块，忽略清理
            pass

    def parse_media(self, share_text: str) -> Dict[str, any]:
        """一次解析抖音分享链接，自动识别视频或图文笔记。"""
        import time
        start_time = time.perf_counter()

        parse_log(logger, "抖音统一解析开始：输入长度=%d", len(share_text or ""))
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', share_text)
        if not urls:
            raise ValueError("未找到有效的分享链接")

        share_url = urls[0]
        parse_log(logger, "抖音统一解析提取到分享链接：%s", short_text(share_url))

        step = time.perf_counter()
        parse_log(logger, "抖音统一解析开始请求短链接并跟随重定向")
        share_response = requests.get(share_url, headers=HEADERS, timeout=10, allow_redirects=True)
        parse_log(
            logger,
            "抖音统一解析短链接请求完成：HTTP %s，最终地址=%s，HTML长度=%d",
            share_response.status_code,
            short_text(share_response.url),
            len(share_response.text or ""),
            step_start=step,
        )

        item_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
        parse_log(logger, "抖音统一解析内容 ID 提取完成：%s", item_id)

        detail_url = f"https://www.iesdouyin.com/share/video/{item_id}"
        step = time.perf_counter()
        parse_log(logger, "抖音统一解析开始请求详情页：%s", short_text(detail_url))
        response = requests.get(detail_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        parse_log(
            logger,
            "抖音统一解析详情页请求完成：HTTP %s，HTML长度=%d",
            response.status_code,
            len(response.text or ""),
            step_start=step,
        )

        pattern = re.compile(
            pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        step = time.perf_counter()
        find_res = pattern.search(response.text)
        if not find_res or not find_res.group(1):
            find_res = pattern.search(share_response.text or "")
            if find_res and find_res.group(1):
                parse_log(logger, "抖音统一解析详情页未命中，改用短链响应 HTML 路由数据")

        if not find_res or not find_res.group(1):
            raise ValueError("从HTML中解析视频或图文信息失败")
        parse_log(logger, "抖音统一解析 HTML 路由数据定位完成", step_start=step)

        step = time.perf_counter()
        json_data = json.loads(find_res.group(1).strip())
        parse_log(logger, "抖音统一解析路由 JSON 解析完成", step_start=step)

        VIDEO_ID_PAGE_KEY = "video_(id)/page"
        NOTE_ID_PAGE_KEY = "note_(id)/page"
        if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
            original_info = json_data["loaderData"][VIDEO_ID_PAGE_KEY]["videoInfoRes"]
        elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
            original_info = json_data["loaderData"][NOTE_ID_PAGE_KEY]["videoInfoRes"]
        else:
            raise Exception("无法从JSON中解析视频或图集信息")

        data = original_info["item_list"][0]
        desc = data.get("desc", "").strip() or f"douyin_{item_id}"
        title = re.sub(r'[\\/:*?"<>|]', '_', desc)

        if "images" in data and data["images"]:
            step = time.perf_counter()
            images = []
            for img in data["images"]:
                if "url_list" in img and img["url_list"]:
                    images.append({
                        "url": img["url_list"][0],
                        "width": img.get("width"),
                        "height": img.get("height"),
                    })
            if not images:
                raise ValueError("无法提取图片URL")
            parse_log(logger, "抖音统一解析图片直链提取完成：%d 张", len(images), step_start=step)
            parse_log(logger, "抖音统一解析完成：图文，图片=%d 张", len(images), flow_start=start_time)
            return {
                "note_id": item_id,
                "title": title,
                "desc": desc,
                "caption": desc,
                "type": "image",
                "images": images,
            }

        if "video" not in data or not data.get("video"):
            raise ValueError("未找到视频信息")

        video_url = data["video"]["play_addr"]["url_list"][0].replace("playwm", "play")
        parse_log(logger, "抖音统一解析完成：视频，直链预览=%s", short_text(video_url), flow_start=start_time)
        return {
            "url": video_url,
            "title": title,
            "caption": desc,
            "video_id": item_id,
            "type": "video",
        }

    def parse_share_url(self, share_text: str) -> Dict[str, str]:
        """从分享文本中提取无水印视频链接"""
        import time
        start_time = time.perf_counter()

        # 提取分享链接
        parse_log(logger, "抖音视频解析开始：输入长度=%d", len(share_text or ""))
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', share_text)
        if not urls:
            raise ValueError("未找到有效的分享链接")

        share_url = urls[0]
        parse_log(logger, "抖音视频提取到分享链接：%s", short_text(share_url))

        t1 = time.perf_counter()
        parse_log(logger, "抖音视频开始请求短链接并跟随重定向")
        share_response = requests.get(share_url, headers=HEADERS, timeout=10, allow_redirects=True)
        parse_log(
            logger,
            "抖音视频短链接请求完成：HTTP %s，最终地址=%s",
            share_response.status_code,
            short_text(share_response.url),
            step_start=t1,
        )

        video_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
        parse_log(logger, "抖音视频 ID 提取完成：%s", video_id)

        share_url = f'https://www.iesdouyin.com/share/video/{video_id}'

        # 获取视频页面内容
        t2 = time.perf_counter()
        parse_log(logger, "抖音视频开始请求详情页：%s", short_text(share_url))
        response = requests.get(share_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        parse_log(
            logger,
            "抖音视频详情页请求完成：HTTP %s，HTML长度=%d",
            response.status_code,
            len(response.text or ""),
            step_start=t2,
        )

        t3 = time.perf_counter()
        pattern = re.compile(
            pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        find_res = pattern.search(response.text)

        if not find_res or not find_res.group(1):
            raise ValueError("从HTML中解析视频信息失败")
        parse_log(logger, "抖音视频 HTML 路由数据定位完成", step_start=t3)

        # 解析JSON数据
        t4 = time.perf_counter()
        json_data = json.loads(find_res.group(1).strip())
        parse_log(logger, "抖音视频路由 JSON 解析完成", step_start=t4)
        VIDEO_ID_PAGE_KEY = "video_(id)/page"
        NOTE_ID_PAGE_KEY = "note_(id)/page"

        if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
            original_video_info = json_data["loaderData"][VIDEO_ID_PAGE_KEY]["videoInfoRes"]
        elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
            original_video_info = json_data["loaderData"][NOTE_ID_PAGE_KEY]["videoInfoRes"]
        else:
            raise Exception("无法从JSON中解析视频或图集信息")

        data = original_video_info["item_list"][0]
        parse_log(logger, "抖音视频 item_list 读取完成，开始判断资源类型")

        # 检查是否为图文笔记
        if "images" in data and data["images"]:
            parse_log(logger, "抖音视频路径发现图片字段，判定为图文笔记")
            raise ValueError("这是图文笔记，请使用 parse_image_note 方法")

        # 检查是否有视频
        if "video" not in data or not data.get("video"):
            raise ValueError("未找到视频信息")

        # 获取视频信息（去水印：playwm -> play）
        video_url = data["video"]["play_addr"]["url_list"][0].replace("playwm", "play")
        raw_desc = data.get("desc", "").strip()
        if not raw_desc:
            raw_desc = f"douyin_{video_id}"

        # 替换文件名中的非法字符，仅用于文件命名
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', raw_desc)

        parse_log(
            logger,
            "抖音视频解析完成：标题长度=%d，直链预览=%s",
            len(raw_desc),
            short_text(video_url),
            flow_start=start_time,
        )

        return {
            "url": video_url,
            "title": safe_title,
            "caption": raw_desc,
            "video_id": video_id
        }

    def parse_image_note(self, share_text: str) -> Dict[str, any]:
        """从分享文本中提取抖音图文笔记，返回图片列表和笔记信息

        返回格式：
        {
            "note_id": str,
            "title": str,
            "desc": str,
            "type": "image",
            "images": [
                {
                    "url": str,  # 无水印图片 URL
                    "width": int,
                    "height": int
                },
                ...
            ]
        }
        """
        import time

        start_time = time.perf_counter()

        # 提取分享链接
        parse_log(logger, "抖音图文解析开始：输入长度=%d", len(share_text or ""))
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', share_text)
        if not urls:
            raise ValueError("未找到有效的分享链接")

        share_url = urls[0]
        parse_log(logger, "抖音图文提取到分享链接：%s", short_text(share_url))

        # 第一次请求：短链接重定向
        t1 = time.perf_counter()
        parse_log(logger, "抖音图文开始请求短链接并跟随重定向")
        share_response = requests.get(share_url, headers=HEADERS, timeout=10, allow_redirects=True)
        parse_log(
            logger,
            "抖音图文短链接请求完成：HTTP %s，最终地址=%s，HTML长度=%d",
            share_response.status_code,
            short_text(share_response.url),
            len(share_response.text or ""),
            step_start=t1,
        )

        note_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
        parse_log(logger, "抖音图文 Note ID 提取完成：%s", note_id)

        # 第二次请求：获取页面内容（实际上第一次请求已经返回了内容，可以直接使用）
        # response = requests.get(share_response.url, headers=HEADERS, timeout=10)
        # 优化：直接使用第一次请求的响应，避免重复请求
        response = share_response
        response.raise_for_status()

        t2 = time.perf_counter()
        pattern = re.compile(
            pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        find_res = pattern.search(response.text)

        if not find_res or not find_res.group(1):
            raise ValueError("从HTML中解析图文信息失败")
        parse_log(logger, "抖音图文 HTML 路由数据定位完成", step_start=t2)

        # 解析JSON数据
        t3 = time.perf_counter()
        json_data = json.loads(find_res.group(1).strip())
        parse_log(logger, "抖音图文路由 JSON 解析完成", step_start=t3)
        NOTE_ID_PAGE_KEY = "note_(id)/page"

        if NOTE_ID_PAGE_KEY not in json_data["loaderData"]:
            raise ValueError("该链接不是图文笔记")

        original_note_info = json_data["loaderData"][NOTE_ID_PAGE_KEY]["videoInfoRes"]
        data = original_note_info["item_list"][0]

        # 检查是否有图片
        if "images" not in data or not data["images"]:
            raise ValueError("该笔记中没有找到图片")

        # 提取图片列表（使用 url_list 获取无水印图片）
        t4 = time.perf_counter()
        images = []
        for img in data["images"]:
            if "url_list" in img and img["url_list"]:
                images.append({
                    "url": img["url_list"][0],  # 使用第一个 URL（无水印版本）
                    "width": img.get("width"),
                    "height": img.get("height")
                })

        if not images:
            raise ValueError("无法提取图片URL")
        parse_log(logger, "抖音图文图片直链提取完成：%d 张", len(images), step_start=t4)

        # 获取标题（抖音图文没有单独的描述字段，desc 就是标题）
        desc = data.get("desc", "").strip() or f"douyin_{note_id}"
        # 替换文件名中的非法字符，仅用于文件命名
        title = re.sub(r'[\\/:*?"<>|]', '_', desc)

        parse_log(
            logger,
            "抖音图文解析完成：图片数量=%d，标题长度=%d",
            len(images),
            len(desc),
            flow_start=start_time,
        )

        return {
            "note_id": note_id,
            "title": title,
            "desc": desc,
            "caption": desc,
            "type": "image",
            "images": images
        }

    async def download_video(self, video_info: dict, ctx: Context) -> Path:
        """异步下载视频到临时目录"""
        filename = f"{video_info['video_id']}.mp4"
        filepath = self.temp_dir / filename

        ctx.info(f"正在下载视频: {video_info['title']}")

        response = requests.get(video_info['url'], headers=HEADERS, stream=True)
        response.raise_for_status()

        # 获取文件大小
        total_size = int(response.headers.get('content-length', 0))

        # 异步下载文件，显示进度
        with open(filepath, 'wb') as f:
            downloaded = 0
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        await ctx.report_progress(downloaded, total_size)

        ctx.info(f"视频下载完成: {filepath}")
        return filepath

    def extract_audio(self, video_path: Path) -> Path:
        """从视频文件中提取音频"""
        audio_path = video_path.with_suffix('.mp3')

        try:
            # 延迟导入，避免非必要依赖阻塞
            import ffmpeg  # noqa: WPS433
            (
                ffmpeg
                .input(str(video_path))
                .output(str(audio_path), acodec='libmp3lame', q=0)
                .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
            )
            return audio_path
        except Exception as e:
            raise Exception(f"提取音频时出错: {str(e)}")

    def extract_text_from_video_url(self, video_url: str) -> str:
        """从视频URL中提取文字（使用阿里云百炼API）"""
        try:
            # 发起异步转录任务
            task_response = dashscope.audio.asr.Transcription.async_call(
                model=self.model,
                file_urls=[video_url],
                language_hints=['zh', 'en']
            )

            # 等待转录完成
            transcription_response = dashscope.audio.asr.Transcription.wait(
                task=task_response.output.task_id
            )

            if transcription_response.status_code == HTTPStatus.OK:
                # 获取转录结果
                for transcription in transcription_response.output['results']:
                    url = transcription['transcription_url']
                    result = json.loads(urlrequest.urlopen(url).read().decode('utf8'))

                    # 保存结果到临时文件
                    temp_json_path = self.temp_dir / 'transcription.json'
                    with open(temp_json_path, 'w') as f:
                        json.dump(result, f, indent=4, ensure_ascii=False)

                    # 提取文本内容
                    if 'transcripts' in result and len(result['transcripts']) > 0:
                        return result['transcripts'][0]['text']
                    else:
                        return "未识别到文本内容"

            else:
                raise Exception(f"转录失败: {transcription_response.output.message}")

        except Exception as e:
            raise Exception(f"提取文字时出错: {str(e)}")

    def cleanup_files(self, *file_paths: Path):
        """清理指定的文件"""
        for file_path in file_paths:
            if file_path.exists():
                file_path.unlink()


if __name__ == "__main__":
    # 便捷测试：
    #   python -m wanyi_watermark.douyin_processor "<douyin_share_url_or_text>"
    import sys
    try:
        share = sys.argv[1]
    except IndexError:
        print("用法: python -m wanyi_watermark.douyin_processor <抖音链接或文本>")
        raise SystemExit(1)

    p = DouyinProcessor("")  # 解析链接无需 API 密钥
    data = p.parse_share_url(share)
    print(json.dumps(data, ensure_ascii=False, indent=2))
