#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PwC China Tax/Business News Flash RSS Feed Generator (Japanese Edition)
毎週金曜日14:00(中国時間)に自動更新
"""

import os
import re
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 定数
URL = "https://www.pwccn.com/en/services/tax/publications/taxlibrary-chinatax-jap.html"
BASE_URL = "https://www.pwccn.com"
OUTPUT_FILE = "rss.xml"
TIMEZONE_CN = timezone(timedelta(hours=8))  # 中国時間 (UTC+8)


def fetch_html(url):
    """HTMLを取得する"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'
        return response.text
    except requests.RequestException as e:
        logger.error(f"HTML取得エラー: {e}")
        return None


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
                # ファイルサイズは取得しない（必要に応じて実装可能）
                fe.enclosure(pub['link'], '0', 'application/pdf')
            except:
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
    html = fetch_html(URL)
    if not html:
        logger.error("HTMLの取得に失敗しました")
        return 1
    
    # デバッグ用：HTMLの一部を保存（必要に応じてコメントアウト）
    # with open('debug.html', 'w', encoding='utf-8') as f:
    #     f.write(html)
    # logger.info("デバッグ用に debug.html を保存しました")
    
    # 出版物情報を抽出
    publications = parse_publications(html)
    if not publications:
        logger.error("出版物が見つかりませんでした")
        logger.info("HTMLの構造を確認してください。デバッグ用に debug.html を保存しました")
        return 1
    
    # RSSを生成
    rss_content = generate_rss(publications)
    
    # 保存
    save_rss(rss_content, OUTPUT_FILE)
    
    logger.info(f"処理完了: {len(publications)} 件の記事をRSSに含めました")
    return 0


if __name__ == "__main__":
    exit(main())
