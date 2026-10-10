#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PwC China Tax/Business News Flash RSS Feed Generator (Japanese Edition)
毎週金曜日14:00(中国時間)に自動更新
"""

import os
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

# ---- オプション依存: cloudscraper（Cloudflare 対策） ----
try:
    import cloudscraper  # type: ignore
    _HAS_CLOUDSCRAPER = True
except ImportError:
    _HAS_CLOUDSCRAPER = False


# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# 定数
URL = "https://www.pwccn.com/en/services/tax/publications/taxlibrary-chinatax-jap.html"
BASE_URL = " https://www.pwccn.com"
OUTPUT_FILE = "rss.xml"
TIMEZONE_CN = timezone(timedelta(hours=8))  # 中国時間 (UTC+8)


# ブラウザに近いヘッダ（403 対策の要）
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8,zh-CN;q=0.7,zh;q=0.6",
    "Referer": " https://www.pwccn.com/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
}


def _build_session():
    """リトライ付きの requests.Session を構築する"""
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,  # 1.5s, 3s, 6s ...
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount(" https://", adapter)
    session.mount("http://", adapter)
    return session


def _fetch_with_requests(session, url, timeout=30):
    """通常の requests で取得"""
    return session.get(url, timeout=timeout, allow_redirects=True)


def _fetch_with_cloudscraper(url, timeout=45):
    """cloudscraper で取得（Cloudflare 対策フォールバック）"""
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    scraper.headers.update(DEFAULT_HEADERS)
    return scraper.get(url, timeout=timeout, allow_redirects=True)


def fetch_html(url):
    """
    HTML を取得する。
    1) requests + リトライで取得
    2) 403/401/503 の場合は cloudscraper でリトライ
    3) 最終手段として Referer を Google 経由に変えて再取得
    4) それでも失敗なら RuntimeError を送出
    """
    session = _build_session()

    # --- 1) requests ---
    try:
        logger.info(f"HTML取得開始: {url}")
        resp = _fetch_with_requests(session, url)
        if resp.status_code == 200 and resp.text:
            resp.encoding = resp.encoding or resp.apparent_encoding
            logger.info(f"HTML取得成功 (requests): {len(resp.text)} bytes")
            return resp.text
        if resp.status_code in (401, 403, 503):
            logger.warning(
                f"requests で HTTP {resp.status_code} を検出。"
                f"cloudscraper へフォールバックします。"
            )
        else:
            logger.warning(f"requests で HTTP {resp.status_code} を受信")
    except requests.exceptions.RequestException as e:
        logger.warning(f"requests での取得に失敗: {e}")

    # --- 2) cloudscraper ---
    if _HAS_CLOUDSCRAPER:
        try:
            logger.info("cloudscraper で再取得を試みます...")
            resp = _fetch_with_cloudscraper(url)
            if resp.status_code == 200 and resp.text:
                resp.encoding = resp.encoding or resp.apparent_encoding
                logger.info(
                    f"HTML取得成功 (cloudscraper): {len(resp.text)} bytes"
                )
                return resp.text
            logger.error(f"cloudscraper でも HTTP {resp.status_code} を受信")
        except Exception as e:
            logger.error(f"cloudscraper での取得に失敗: {e}")
    else:
        logger.warning(
            "cloudscraper がインストールされていません。"
            "pip install cloudscraper で導入すると成功率が上がります。"
        )

    # --- 3) 最終リトライ: Referer を Google 経由に偽装 ---
    try:
        logger.info("最終リトライ: 5秒待機後に requests で再取得")
        time.sleep(5)
        session.headers.update(
            {"Referer": " https://www.google.com/search?q=pwccn+tax+library"}
        )
        resp = _fetch_with_requests(session, url)
        if resp.status_code == 200 and resp.text:
            resp.encoding = resp.encoding or resp.apparent_encoding
            logger.info(f"HTML取得成功 (最終リトライ): {len(resp.text)} bytes")
            return resp.text
        logger.error(f"最終リトライでも HTTP {resp.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"最終リトライに失敗: {e}")

    raise RuntimeError(f"HTMLの取得に失敗しました（全手段を試行後）: {url}")


def parse_publications(html):
    """HTMLから出版物情報を抽出する"""
    soup = BeautifulSoup(html, 'html.parser')
    publications = []

    # すべてのテーブルを検索
    tables = soup.find_all('table')
    logger.info(f"テーブル数: {len(tables)}")

    # 各テーブルに対して処理
    for table in tables:
        # テーブルの直前のh3タグを探して年を特定
        prev_h3 = table.find_previous('h3')
        if not prev_h3:
            continue

        year_text = prev_h3.get_text(strip=True)
        # 年（4桁の数字）を抽出
        year_match = re.search(r'\d{4}', year_text)
        if not year_match:
            continue

        year = year_match.group()
        logger.info(f"処理中の年: {year}")

        # テーブルの行を解析（ヘッダー行をスキップ）
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 3:
                continue

            # 日付、号数、リンクを抽出
            date_cell = cells[0].get_text(strip=True)
            issue_cell = cells[1].get_text(strip=True) if len(cells) > 1 else ""

            # 3番目のセルにリンクがある
            link_cell = cells[2]
            link_tag = link_cell.find('a')

            if not link_tag:
                # リンクがない行はスキップ（ヘッダー行など）
                continue

            title = link_tag.get_text(strip=True)
            link = link_tag.get('href', '')

            # 相対URLを絶対URLに変換
            if link and not link.startswith('http'):
                link = urljoin(BASE_URL, link)

            # 空のリンクやタイトルはスキップ
            if not link or not title:
                continue

            # PDFファイルかどうかチェック（拡張子が.pdf）
            is_pdf = link.lower().endswith('.pdf')

            # 日付をパース
            try:
                pub_date = parse_date(date_cell, year)
            except ValueError as e:
                logger.warning(f"日付パースエラー: {date_cell} - {e}")
                # デフォルトで年の初日を使用
                pub_date = datetime(int(year), 1, 1, tzinfo=TIMEZONE_CN)

            publication = {
                'title': title,
                'link': link,
                'pub_date': pub_date,
                'date_display': date_cell,
                'issue': issue_cell,
                'year': year,
                'is_pdf': is_pdf
            }
            publications.append(publication)
            logger.info(f"  記事を追加: {title[:50]}... ({date_cell})")

    # 日付でソート（新しい順）
    publications.sort(key=lambda x: x['pub_date'], reverse=True)

    logger.info(f"合計 {len(publications)} 件の出版物を抽出しました")
    return publications


def parse_date(date_str, year):
    """日付文字列をパースする（例: 'Mar 2026' -> datetime）"""
    month_map = {
        'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4,
        'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8,
        'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
        'January': 1, 'February': 2, 'March': 3, 'April': 4,
        'May': 5, 'June': 6, 'July': 7, 'August': 8,
        'September': 9, 'October': 10, 'November': 11, 'December': 12
    }

    # 日付文字列をクリーニング
    date_str = date_str.strip()

    # "Mar 2026" または "March 2026" などの形式
    parts = date_str.split()
    if len(parts) >= 2:
        month_str = parts[0]
        year_str = parts[1]
        # 年の数字部分を抽出
        year_match = re.search(r'\d+', year_str)
        if year_match:
            year_num = int(year_match.group())
            if year_num < 100:
                year_num += 2000
        else:
            year_num = int(year)
    else:
        # 日付が月だけの場合は年から取得
        year_num = int(year)
        month_str = date_str

    # 月を数値に変換（部分一致も対応）
    month = 1
    for key, value in month_map.items():
        if month_str.startswith(key[:3]):
            month = value
            break

    return datetime(year_num, month, 1, tzinfo=TIMEZONE_CN)


def generate_rss(publications):
    """RSSフィードを生成する"""
    fg = FeedGenerator()
    fg.title('PwC China Tax/Business News Flash (日本語版)')
    fg.description('PwC中国のタックス・ナレッジ・マネジメントセンターが発行する中国税務・ビジネスニュース（日本語版）')
    fg.link(href=URL, rel='alternate')
    fg.language('ja')

    # フィードのデフォルトリンクを設定
    fg.link(href=URL, rel='self')

    # 最終更新日を設定
    if publications:
        latest_date = max(p['pub_date'] for p in publications)
        fg.lastBuildDate(latest_date)

    # 各出版物をRSSアイテムとして追加
    for pub in publications:
        fe = fg.add_entry()
        fe.title(pub['title'])
        fe.link(href=pub['link'])
        fe.pubDate(pub['pub_date'])
        fe.guid(pub['link'], permalink=True)

        # 説明を生成
        description_parts = []
        description_parts.append(f"発行日: {pub['date_display']}")
        if pub['issue']:
            description_parts.append(f"Issue: {pub['issue']}")
        if pub['is_pdf']:
            description_parts.append("[ステッカー] PDFファイル")

        description = " | ".join(description_parts)
        fe.description(description)

        # PDFの場合はエンクロージャとして設定（オプション）
        if pub['is_pdf'] and pub['link'].endswith('.pdf'):
            try:
                fe.enclosure(pub['link'], '0', 'application/pdf')
            except Exception:
                pass

    return fg.rss_str(pretty=True)


def save_rss(rss_content, output_file):
    """RSSファイルを保存する"""
    try:
        with open(output_file, 'wb') as f:
            f.write(rss_content)
        logger.info(f"RSSフィードを保存しました: {output_file}")
    except Exception as e:
        logger.error(f"RSS保存エラー: {e}")
        raise


def main():
    """メイン処理"""
    logger.info("PwC中国 税務ニュース RSS生成を開始")

    # HTMLを取得
    try:
        html = fetch_html(URL)
    except RuntimeError as e:
        logger.error(str(e))
        return 1

    # デバッグ用：HTMLの一部を保存（必要に応じてコメントアウト）
    # with open('debug.html', 'w', encoding='utf-8') as f:
    #     f.write(html)
    # logger.info("デバッグ用に debug.html を保存しました")

    # 出版物情報を抽出
    publications = parse_publications(html)
    if not publications:
        logger.error("出版物が見つかりませんでした")
        logger.info("HTMLの構造を確認してください。")
        # デバッグ用に HTML を保存（Actions の artifact で確認可能）
        try:
            with open('debug.html', 'w', encoding='utf-8') as f:
                f.write(html)
            logger.info("デバッグ用に debug.html を保存しました")
        except Exception:
            pass
        return 1

    # RSSを生成
    rss_content = generate_rss(publications)

    # 保存
    save_rss(rss_content, OUTPUT_FILE)

    logger.info(f"処理完了: {len(publications)} 件の記事をRSSに含めました")
    return 0


if __name__ == "__main__":
    exit(main())
