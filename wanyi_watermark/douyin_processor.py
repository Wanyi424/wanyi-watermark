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

    def parse_share_url(self, share_text: str) -> Dict[str, str]:
        """从分享文本中提取无水印视频链接"""
        import time
        start_time = time.time()

        # 提取分享链接
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', share_text)
        if not urls:
            raise ValueError("未找到有效的分享链接")

        share_url = urls[0]
        logger.debug(f"[抖音视频] 提取到的链接: {share_url}")

        t1 = time.time()
        share_response = requests.get(share_url, headers=HEADERS, timeout=10, allow_redirects=True)
        logger.debug(f"[抖音视频] 短链接重定向耗时: {time.time()-t1:.2f}秒")

        video_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
        logger.debug(f"[抖音视频] 视频ID: {video_id}")

        share_url = f'https://www.iesdouyin.com/share/video/{video_id}'

        # 获取视频页面内容
        t2 = time.time()
        response = requests.get(share_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        logger.debug(f"[抖音视频] 页面请求耗时: {time.time()-t2:.2f}秒")

        pattern = re.compile(
            pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        find_res = pattern.search(response.text)

        if not find_res or not find_res.group(1):
            raise ValueError("从HTML中解析视频信息失败")

        # 解析JSON数据
        json_data = json.loads(find_res.group(1).strip())
        VIDEO_ID_PAGE_KEY = "video_(id)/page"
        NOTE_ID_PAGE_KEY = "note_(id)/page"

        if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
            original_video_info = json_data["loaderData"][VIDEO_ID_PAGE_KEY]["videoInfoRes"]
        elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
            original_video_info = json_data["loaderData"][NOTE_ID_PAGE_KEY]["videoInfoRes"]
        else:
            raise Exception("无法从JSON中解析视频或图集信息")

        data = original_video_info["item_list"][0]

        # 检查是否为图文笔记
        if "images" in data and data["images"]:
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

        total_time = time.time() - start_time
        logger.debug(f"[抖音视频] 解析完成，总耗时: {total_time:.2f}秒")
        logger.debug(f"{'='*60}\n")

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

        start_time = time.time()

        # 提取分享链接
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', share_text)
        if not urls:
            raise ValueError("未找到有效的分享链接")

        share_url = urls[0]
        logger.debug(f"[抖音图文] 开始处理短链接: {share_url}")

        # 第一次请求：短链接重定向
        t1 = time.time()
        share_response = requests.get(share_url, headers=HEADERS, timeout=10, allow_redirects=True)
        logger.debug(f"[抖音图文] 短链接重定向耗时: {time.time()-t1:.2f}秒")

        note_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
        logger.debug(f"[抖音图文] Note ID: {note_id}")

        # 第二次请求：获取页面内容（实际上第一次请求已经返回了内容，可以直接使用）
        # response = requests.get(share_response.url, headers=HEADERS, timeout=10)
        # 优化：直接使用第一次请求的响应，避免重复请求
        response = share_response
        response.raise_for_status()

        pattern = re.compile(
            pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        find_res = pattern.search(response.text)

        if not find_res or not find_res.group(1):
            raise ValueError("从HTML中解析图文信息失败")

        # 解析JSON数据
        json_data = json.loads(find_res.group(1).strip())
        NOTE_ID_PAGE_KEY = "note_(id)/page"

        if NOTE_ID_PAGE_KEY not in json_data["loaderData"]:
            raise ValueError("该链接不是图文笔记")

        original_note_info = json_data["loaderData"][NOTE_ID_PAGE_KEY]["videoInfoRes"]
        data = original_note_info["item_list"][0]

        # 检查是否有图片
        if "images" not in data or not data["images"]:
            raise ValueError("该笔记中没有找到图片")

        # 提取图片列表（使用 url_list 获取无水印图片）
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

        # 获取标题（抖音图文没有单独的描述字段，desc 就是标题）
        desc = data.get("desc", "").strip() or f"douyin_{note_id}"
        # 替换文件名中的非法字符，仅用于文件命名
        title = re.sub(r'[\\/:*?"<>|]', '_', desc)

        total_time = time.time() - start_time
        logger.debug(f"[抖音图文] 解析完成，总耗时: {total_time:.2f}秒，图片数量: {len(images)}")
        logger.debug(f"{'='*60}\n")

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

