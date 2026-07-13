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
from collections import defaultdict

# === CONFIG ===
CONFIG_PATH = os.path.expanduser("~/oos-monitor/config.json")
STATE_PATH = os.path.expanduser("~/oos-monitor/oos_state.json")
DASHBOARD_DATA_PATH = os.path.expanduser("~/oos-monitor/dashboard_data.json")
DISCORD_WEBHOOK_PATH = os.path.expanduser("~/oos-monitor/discord_webhook.txt")
LOW_STOCK_THRESHOLD = 10  # Alert when stock < 10
LOW_STOCK_THRESHOLD_30 = 30  # Alert when stock < 30

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

def extract_price(page, add_to_cart_buttons):
    """Extract selling price from add-to-cart button's data-price attribute."""
    try:
        if add_to_cart_buttons.count() > 0:
            price_attr = add_to_cart_buttons.first.get_attribute("data-price")
            if price_attr:
                # data-price format: "$ 359.00" → extract "359.00"
                import re
                match = re.search(r'([0-9,]+\.[0-9]{2})', price_attr)
                if match:
                    return match.group(1)
    except:
        pass
    return ""

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
            status = "super_low_stock"
            reason = f"尚餘{stock_level}件-不足10PCS"
        elif stock_level is not None and stock_level < LOW_STOCK_THRESHOLD_30:
            status = "low_stock"
            reason = f"尚餘{stock_level}件-不足30PCS"
        else:
            status = "in_stock"
            reason = "加入購物車 available"
        
        return {
            "sku": sku_id,
            "status": status,
            "reason": reason,
            "product_name": product_name[:100],
            "price": extract_price(page, add_to_cart_buttons),
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
    elif status == "super_low_stock":
        emoji = "🔴"
        title = f"⚠️ 超低庫存通知 Super Low Stock Alert (< 10 PCS — 僅餘{stock_level}件)"
        desc = f"**{product_name}** 僅剩 **{stock_level}** 件（不足10件）！" if product_name else f"**{sku}** 僅剩 **{stock_level}** 件（不足10件）！"
        color = 0xFF4444
    elif status == "low_stock":
        emoji = "🟡"
        title = f"⚠️ 低庫存通知 Low Stock Alert (< 30 PCS — 尚餘{stock_level}件)"
        desc = f"**{product_name}** 僅剩 **{stock_level}** 件（不足30件）！" if product_name else f"**{sku}** 僅剩 **{stock_level}** 件（不足30件）！"
        color = 0xFFAA44
    elif status == "in_stock" and prev_status in ("oos", "super_low_stock", "low_stock"):
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
    """Check monitoring period for a SKU. Returns 'ok', 'pending' (not started), or 'expired' (past end)."""
    now = datetime.now()
    
    if sku:
        # Check per-SKU start period (if set, only check after this time)
        start_periods = config.get("sku_start_periods", {})
        sku_start = start_periods.get(sku)
        if sku_start:
            try:
                start_dt = datetime.fromisoformat(sku_start)
                if now < start_dt:
                    return "pending"  # Not yet active
            except:
                pass
        
        # Check per-SKU end period
        end_periods = config.get("sku_periods", {})
        sku_end = end_periods.get(sku)
        if sku_end:
            try:
                end_dt = datetime.fromisoformat(sku_end)
                if now > end_dt:
                    return "expired"  # Past end
                return "ok"  # Per-SKU end in future — SKU is active, skip global
            except:
                pass
    
    # Fall back to global period
    monitor_end = config.get("monitor_end", "")
    if monitor_end:
        try:
            end_dt = datetime.fromisoformat(monitor_end)
            if now > end_dt:
                return "expired"
        except:
            pass
    return "ok"

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
            period_status = check_monitor_period(config, sku)
            if period_status == "pending":
                prev_state = state.get(sku, {})
                if prev_state.get("product_name"):
                    print(f"  ⏳ {sku}... [pending — cached: {prev_state.get('product_name', '')[:40]}]")
                    results.append({
                        "sku": sku,
                        "status": "pending",
                        "reason": "Not yet started",
                        "product_name": prev_state["product_name"],
                        "price": prev_state.get("price", ""),
                        "stock_level": prev_state.get("stock_level"),
                        "checked_at": datetime.now().isoformat()
                    })
                else:
                    # First time seeing this SKU — do a quick scan for product info
                    print(f"  ⏳ {sku}... [pending — scanning for product info...]", end=" ", flush=True)
                    try:
                        page.goto(f"https://www.hktvmall.com/hktv/p/{sku}", timeout=25000, wait_until="networkidle")
                        page.wait_for_timeout(3000)
                        title = page.title().strip()
                        html_content = page.content()
                        
                        # Validate title — skip error/redirect pages
                        if "Oops" in title or "502" in title or "404" in title or "Error" in title or "| HKTVmall" not in title:
                            product_name = ""
                            stock_level = None
                            price = ""
                            print(f"[INVALID PAGE: {title[:30]}]")
                        else:
                            product_name = title.replace(" | HKTVmall 香港最大網購平台", "").strip()[:100]
                            stock_level = extract_stock_level(html_content)
                            # Extract price from data-price attribute in HTML
                            pm = re.search(r'data-price="[^\d]*(\d+\.\d{2})', html_content)
                            if pm:
                                price = pm.group(1)
                            else:
                                # Fallback: JSON-LD price
                                pm2 = re.search(r'"price"\s*:\s*"(\d+\.?\d*)"', html_content)
                                price = pm2.group(1) if pm2 else ""
                            print(f"[{product_name[:30]} ${price}]")
                        # Cache in state (only if valid)
                        if product_name:
                            state[sku] = {
                                "status": "pending",
                                "product_name": product_name,
                                "stock_level": stock_level,
                                "price": price,
                                "last_change": "",
                                "last_checked": datetime.now().isoformat()
                            }
                        results.append({
                            "sku": sku,
                            "status": "pending",
                            "reason": "Not yet started",
                            "product_name": product_name,
                            "price": price,
                            "stock_level": stock_level,
                            "checked_at": datetime.now().isoformat()
                        })
                    except Exception as e:
                        print(f"[ERROR: {str(e)[:30]}]")
                        results.append({
                            "sku": sku,
                            "status": "pending",
                            "reason": "Not yet started",
                            "product_name": "",
                            "price": "",
                            "stock_level": None,
                            "checked_at": datetime.now().isoformat()
                        })
                continue
            elif period_status == "expired":
                # Use last known data from state for expired SKUs
                prev_state = state.get(sku, {})
                print(f"  ⏰ {sku}... [expired — last known: {prev_state.get('product_name', '')[:40]}]")
                results.append({
                    "sku": sku,
                    "status": "expired",
                    "reason": "Monitor period ended",
                    "product_name": prev_state.get("product_name", ""),
                    "price": prev_state.get("price", ""),
                    "stock_level": prev_state.get("stock_level"),
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
            
            if result["status"] in ("oos", "super_low_stock", "low_stock", "in_stock") and result["status"] != prev_status:
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
                "price": result.get("price", ""),
                "last_change": datetime.now().isoformat() if result["status"] != prev_status else prev.get("last_change"),
                "last_checked": datetime.now().isoformat()
            }
        
        browser.close()
    
    # Update dashboard data
    oos_list = [
        {"sku": r["sku"], "product_name": r["product_name"], "price": r["price"], "checked_at": r["checked_at"]}
        for r in results if r["status"] == "oos"
    ]
    super_low_stock_list = [
        {"sku": r["sku"], "product_name": r["product_name"], "price": r["price"], 
         "stock_level": r["stock_level"], "checked_at": r["checked_at"]}
        for r in results if r["status"] == "super_low_stock"
    ]
    low_stock_30_list = [
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
    dashboard["super_low_stock_skus"] = super_low_stock_list
    dashboard["low_stock_30_skus"] = low_stock_30_list
    dashboard["all_skus"] = all_skus_list
    dashboard["last_checked"] = datetime.now().isoformat()
    
    # Add to history with per-SKU limit (keep last 1000 entries per SKU)
    for r in results:
        if r["status"] in ("oos", "super_low_stock", "low_stock", "in_stock"):
            dashboard["history"].append({
                "sku": r["sku"],
                "product_name": r["product_name"],
                "status": r["status"],
                "stock_level": r.get("stock_level"),
                "checked_at": r["checked_at"]
            })
    # Per-SKU limit: keep last 1000 entries for each SKU
    sku_groups = defaultdict(list)
    for h in dashboard["history"]:
        sku_groups[h["sku"]].append(h)
    dashboard["history"] = []
    for sku, entries in sku_groups.items():
        dashboard["history"].extend(entries[-1000:])
    
    save_state(state)
    save_dashboard_data(dashboard)
    
    # Summary
    in_stock = sum(1 for r in results if r["status"] == "in_stock")
    super_low_stock = sum(1 for r in results if r["status"] == "super_low_stock")
    low_stock = sum(1 for r in results if r["status"] == "low_stock")
    oos_count = sum(1 for r in results if r["status"] == "oos")
    pending = sum(1 for r in results if r["status"] == "pending")
    expired = sum(1 for r in results if r["status"] == "expired")
    errors = sum(1 for r in results if r["status"] == "error")
    
    print(f"\n✅ Done — In Stock: {in_stock}, Super Low: {super_low_stock}, Low: {low_stock}, OOS: {oos_count}, Pending: {pending}, Expired: {expired}, Errors: {errors}")
    
    # Output alerts
    if super_low_stock > 0:
        print(f"\n🔴 SUPER LOW STOCK (<10) SKUs:")
        for r in results:
            if r["status"] == "super_low_stock":
                print(f"  🔴 {r['sku']} — {r['product_name'][:60]} (only {r['stock_level']} left)")
    if low_stock > 0:
        print(f"\n🟡 Low Stock (<30) SKUs:")
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
