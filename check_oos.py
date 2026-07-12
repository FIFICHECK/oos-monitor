#!/usr/bin/env python3
"""
HKTVmall OOS (Out of Stock) Monitor
- Uses Playwright to check product pages
- Detects if "加入購物車" button is present (in stock) vs OOS indicators
- Detects low stock (< 10 items) → "尚餘少量"
- Reports to Discord and dashboard JSON
"""

import json
import os
import sys
import time
import re
from datetime import datetime
from pathlib import Path

# === CONFIG ===
CONFIG_PATH = os.path.expanduser("~/oos-monitor/config.json")
STATE_PATH = os.path.expanduser("~/oos-monitor/oos_state.json")
DASHBOARD_DATA_PATH = os.path.expanduser("~/oos-monitor/dashboard_data.json")
DISCORD_WEBHOOK_PATH = os.path.expanduser("~/oos-monitor/discord_webhook.txt")
LOW_STOCK_THRESHOLD = 10  # Alert when stock < 10

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}  # {sku: {"status": "in_stock"|"low_stock"|"oos", ...}}

def save_state(state):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_dashboard_data():
    if os.path.exists(DASHBOARD_DATA_PATH):
        with open(DASHBOARD_DATA_PATH) as f:
            return json.load(f)
    return {"oos_skus": [], "low_stock_skus": [], "all_skus": [], "last_checked": None, "history": []}

def save_dashboard_data(data):
    with open(DASHBOARD_DATA_PATH, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def extract_stock_level(html_content):
    """Extract stockLevel from page HTML content."""
    # Pattern: "stockLevel":123
    match = re.search(r'"stockLevel"\s*:\s*(\d+)', html_content)
    if match:
        return int(match.group(1))
    return None

def extract_price(body_text):
    """Extract price from page text."""
    prices = re.findall(r'\$\s*([0-9,]+\.[0-9]{2})', body_text)
    return prices[0] if prices else ""

def check_sku(page, sku_id):
    """Check if a SKU is in stock, low stock, or OOS on HKTVmall."""
    url = f"https://www.hktvmall.com/hktv/p/{sku_id}"
    
    try:
        page.goto(url, wait_until="networkidle", timeout=25000)
        time.sleep(2)  # Extra wait for JS rendering
        
        body_text = page.inner_text("body")
        html_content = page.content()
        
        # Check for in-stock indicators
        add_to_cart_buttons = page.locator("button:has-text('加入購物車')")
        has_add_to_cart = add_to_cart_buttons.count() > 0
        
        # Check for OOS indicators
        has_oos_text = any(t in body_text for t in [
            "暫時缺貨", "已售罄", "售罄", "缺貨"
        ])
        
        # Check if button is disabled
        button_disabled = False
        if has_add_to_cart:
            button_disabled = add_to_cart_buttons.first.is_disabled()
        
        # Get product name from title
        title = page.title()
        product_name = title.replace(" | HKTVmall 香港最大網購平台", "").strip()
        
        # Extract stock level
        stock_level = extract_stock_level(html_content)
        
        # Determine status
        if has_oos_text or (not has_add_to_cart) or button_disabled:
            status = "oos"
            reason = "OOS indicator found"
        elif stock_level is not None and stock_level < LOW_STOCK_THRESHOLD:
            status = "low_stock"
            reason = f"尚餘{stock_level}件"
        else:
            status = "in_stock"
            reason = "加入購物車 available"
        
        return {
            "sku": sku_id,
            "status": status,
            "reason": reason,
            "product_name": product_name[:100],
            "price": extract_price(body_text),
            "stock_level": stock_level,
            "checked_at": datetime.now().isoformat()
        }
    
    except Exception as e:
        return {
            "sku": sku_id,
            "status": "error",
            "reason": str(e)[:200],
            "product_name": "",
            "price": "",
            "stock_level": None,
            "checked_at": datetime.now().isoformat()
        }

def send_discord_notification(sku, product_name, status, prev_status, stock_level=None):
    """Send Discord notification via webhook."""
    webhook_url = ""
    if os.path.exists(DISCORD_WEBHOOK_PATH):
        with open(DISCORD_WEBHOOK_PATH) as f:
            webhook_url = f.read().strip()
    
    if not webhook_url:
        print(f"  (Discord webhook not configured — notification skipped)")
        return
    
    import urllib.request
    
    if status == "oos":
        emoji = "🔴"
        title = "⚠️ 缺貨通知 Out of Stock Alert"
        desc = f"**{product_name}** 已缺貨！" if product_name else f"**{sku}** 已缺貨！"
        color = 0xFF4444
    elif status == "low_stock":
        emoji = "🟡"
        title = f"⚠️ 低庫存通知 Low Stock Alert (尚餘{stock_level}件)"
        desc = f"**{product_name}** 僅剩 **{stock_level}** 件！" if product_name else f"**{sku}** 僅剩 **{stock_level}** 件！"
        color = 0xFFAA44
    elif status == "in_stock" and prev_status in ("oos", "low_stock"):
        emoji = "🟢"
        title = "✅ 返貨通知 Back in Stock!"
        desc = f"**{product_name}** 已恢復庫存！" if product_name else f"**{sku}** 已恢復庫存！"
        color = 0x44FF44
    else:
        return  # No change, no notification
    
    fields = [
        {"name": "SKU", "value": sku, "inline": True},
        {"name": "Status", "value": status, "inline": True},
    ]
    if stock_level is not None:
        fields.append({"name": "庫存", "value": f"{stock_level} 件", "inline": True})
    fields.append({"name": "檢查時間", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "inline": False})
    fields.append({"name": "Product Link", "value": f"https://www.hktvmall.com/hktv/p/{sku}", "inline": False})
    
    payload = {
        "embeds": [{
            "title": f"{emoji} {title}",
            "description": desc,
            "fields": fields,
            "color": color,
            "timestamp": datetime.now().isoformat()
        }]
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={'Content-Type': 'application/json'}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"  ✅ Discord notification sent for {sku}")
    except Exception as e:
        print(f"  ❌ Failed to send Discord notification: {e}")

def check_monitor_period(config, sku=None):
    """Check if current time is within the monitoring period for a SKU (or globally)."""
    # First check per-SKU period
    if sku:
        periods = config.get("sku_periods", {})
        sku_end = periods.get(sku)
        if sku_end:
            try:
                end_dt = datetime.fromisoformat(sku_end)
                if datetime.now() > end_dt:
                    return False
            except:
                pass
    
    # Fall back to global period
    monitor_end = config.get("monitor_end", "")
    if monitor_end:
        try:
            end_dt = datetime.fromisoformat(monitor_end)
            if datetime.now() > end_dt:
                return False
        except:
            pass
    return True

def main():
    config = load_config()
    state = load_state()
    dashboard = load_dashboard_data()
    
    if not check_monitor_period(config):
        return 0
    
    print(f"🔍 OOS Monitor — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📋 Checking {len(config['skus'])} SKU(s)...")
    print(f"📅 Monitor until: {config.get('monitor_end', 'N/A')}")
    
    from playwright.sync_api import sync_playwright
    
    results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        for sku in config['skus']:
            # Check per-SKU period
            if not check_monitor_period(config, sku):
                print(f"  ⏰ {sku}... [expired — monitor period ended]")
                results.append({
                    "sku": sku,
                    "status": "expired",
                    "reason": "Monitor period ended",
                    "product_name": "",
                    "price": "",
                    "stock_level": None,
                    "checked_at": datetime.now().isoformat()
                })
                continue
            
            print(f"  🔎 {sku}...", end=" ", flush=True)
            result = check_sku(page, sku)
            results.append(result)
            
            # Show result
            status_icon = {"in_stock": "✅", "low_stock": "🟡", "oos": "🔴", "error": "❌"}.get(result["status"], "❓")
            stock_info = f" (stock:{result['stock_level']})" if result['stock_level'] is not None else ""
            print(f"[{status_icon} {result['status']}{stock_info}] {result.get('product_name', '')[:40]}")
            
            # Check for status change
            prev = state.get(sku, {})
            prev_status = prev.get("status", "unknown")
            
            if result["status"] in ("oos", "low_stock", "in_stock") and result["status"] != prev_status:
                print(f"  ⚡ Status changed: {prev_status} → {result['status']}")
                send_discord_notification(
                    sku, result["product_name"],
                    result["status"], prev_status,
                    result.get("stock_level")
                )
            elif result["status"] == "low_stock" and prev_status == "low_stock":
                # Still low stock but quantity changed significantly?
                prev_stock = prev.get("stock_level", 0)
                current_stock = result.get("stock_level", 0)
                if current_stock != prev_stock:
                    print(f"  ℹ️  Stock updated: {prev_stock} → {current_stock}")
            
            # Update state
            state[sku] = {
                "status": result["status"],
                "product_name": result["product_name"],
                "stock_level": result.get("stock_level"),
                "last_change": datetime.now().isoformat() if result["status"] != prev_status else prev.get("last_change"),
                "last_checked": datetime.now().isoformat()
            }
        
        browser.close()
    
    # Update dashboard data
    oos_list = [
        {"sku": r["sku"], "product_name": r["product_name"], "price": r["price"], "checked_at": r["checked_at"]}
        for r in results if r["status"] == "oos"
    ]
    low_stock_list = [
        {"sku": r["sku"], "product_name": r["product_name"], "price": r["price"], 
         "stock_level": r["stock_level"], "checked_at": r["checked_at"]}
        for r in results if r["status"] == "low_stock"
    ]
    all_skus_list = [
        {
            "sku": r["sku"],
            "product_name": r["product_name"],
            "status": r["status"],
            "stock_level": r.get("stock_level"),
            "price": r["price"],
            "checked_at": r["checked_at"]
        }
        for r in results
    ]
    
    dashboard["oos_skus"] = oos_list
    dashboard["low_stock_skus"] = low_stock_list
    dashboard["all_skus"] = all_skus_list
    dashboard["last_checked"] = datetime.now().isoformat()
    
    # Add to history (keep last 100 events)
    for r in results:
        if r["status"] in ("oos", "low_stock", "in_stock"):
            dashboard["history"].append({
                "sku": r["sku"],
                "product_name": r["product_name"],
                "status": r["status"],
                "stock_level": r.get("stock_level"),
                "checked_at": r["checked_at"]
            })
    dashboard["history"] = dashboard["history"][-100:]  # Keep last 100
    
    save_state(state)
    save_dashboard_data(dashboard)
    
    # Summary
    in_stock = sum(1 for r in results if r["status"] == "in_stock")
    low_stock = sum(1 for r in results if r["status"] == "low_stock")
    oos_count = sum(1 for r in results if r["status"] == "oos")
    expired = sum(1 for r in results if r["status"] == "expired")
    errors = sum(1 for r in results if r["status"] == "error")
    
    print(f"\n✅ Done — In Stock: {in_stock}, Low Stock: {low_stock}, OOS: {oos_count}, Expired: {expired}, Errors: {errors}")
    
    # Output alerts
    if low_stock > 0:
        print(f"\n🟡 Low Stock SKUs:")
        for r in results:
            if r["status"] == "low_stock":
                print(f"  🟡 {r['sku']} — {r['product_name'][:60]} (only {r['stock_level']} left)")
    if oos_count > 0:
        print(f"\n🔴 OOS SKUs:")
        for r in results:
            if r["status"] == "oos":
                print(f"  🔴 {r['sku']} — {r['product_name'][:60]}")
    
    return 0 if errors == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
