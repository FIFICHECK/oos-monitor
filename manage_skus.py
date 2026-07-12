#!/usr/bin/env python3
"""
OOS Monitor — SKU Management CLI
用法: python3 ~/oos-monitor/manage_skus.py <command> [sku] [options]

Commands:
  list                          — 列出所有監察中的 SKU
  add B0961005_S_XXXX           — 加入 SKU (預設 period 跟 config.monitor_end)
  add B0961005_S_XXXX --period 2026-07-13T23:59:59  — 加入 SKU + 自訂 period
  remove B0961005_S_XXXX        — 移除 SKU
  period B0961005_S_XXXX 2026-07-13T23:59:59  — 修改 SKU 嘅 monitor period
  add-bulk                      — 從 stdin 一次過加多個 SKU

例子:
  python3 ~/oos-monitor/manage_skus.py add B0961005_S_4891609190294
  python3 ~/oos-monitor/manage_skus.py add B0961005_S_XXX --period 2026-07-13T23:59:59
  python3 ~/oos-monitor/manage_skus.py period B0961005_S_XXX 2026-07-13T23:59:59
  python3 ~/oos-monitor/manage_skus.py list
"""

import json, os, sys
from pathlib import Path

CONFIG_PATH = os.path.expanduser("~/oos-monitor/config.json")

def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def get_sku_period(config, sku):
    """Get period for a SKU, fallback to global monitor_end."""
    periods = config.get("sku_periods", {})
    return periods.get(sku, config.get("monitor_end", "N/A"))

def cmd_list(config):
    skus = config.get('skus', [])
    if not skus:
        print("📭 No SKUs configured.")
        return
    print(f"📋 Monitored SKUs ({len(skus)}):")
    print("─" * 70)
    for i, sku in enumerate(skus, 1):
        period = get_sku_period(config, sku)
        print(f"  {i:2d}. {sku}  ⏱️  Until {period}")
    print("─" * 70)

def cmd_add(config, sku, period=None):
    if not sku:
        print("❌ Usage: python3 manage_skus.py add B0961005_S_XXXXXXXXXXXX [--period TIME]")
        return 1
    if not sku.startswith("B0961005_S_"):
        print(f"⚠️  Warning: SKU should start with B0961005_S_")
    
    skus = config.get('skus', [])
    if sku in skus:
        print(f"ℹ️  '{sku}' already in list")
        return 0
    
    skus.append(sku)
    config['skus'] = skus
    
    # Set per-SKU period if provided
    if period:
        if "sku_periods" not in config:
            config["sku_periods"] = {}
        config["sku_periods"][sku] = period
    
    save_config(config)
    
    display_period = period or config.get("monitor_end", "N/A")
    print(f"✅ Added: {sku}")
    print(f"   ⏱️  Period: Until {display_period}")
    print(f"📋 Total: {len(skus)} SKU(s)")
    return 0

def cmd_remove(config, sku):
    if not sku:
        print("❌ Usage: python3 manage_skus.py remove B0961005_S_XXXXXXXXXXXX")
        return 1
    
    skus = config.get('skus', [])
    if sku not in skus:
        print(f"❌ '{sku}' not found in list")
        return 1
    
    skus.remove(sku)
    config['skus'] = skus
    
    # Clean up period entry
    if "sku_periods" in config and sku in config["sku_periods"]:
        del config["sku_periods"][sku]
    
    save_config(config)
    print(f"✅ Removed: {sku}")
    print(f"📋 Total: {len(skus)} SKU(s)")
    return 0

def cmd_period(config, sku, period):
    if not sku or not period:
        print("❌ Usage: python3 manage_skus.py period B0961005_S_XXX 2026-07-13T23:59:59")
        return 1
    
    skus = config.get('skus', [])
    if sku not in skus:
        print(f"❌ '{sku}' not found in list")
        return 1
    
    if "sku_periods" not in config:
        config["sku_periods"] = {}
    config["sku_periods"][sku] = period
    save_config(config)
    print(f"✅ Updated period for {sku}: Until {period}")
    return 0

def cmd_add_bulk(config):
    skus = config.get('skus', [])
    added = 0
    duplicates = 0
    
    for line in sys.stdin:
        parts = line.strip().split()
        if not parts:
            continue
        sku = parts[0]
        period = parts[1] if len(parts) > 1 else None
        
        if sku in skus:
            duplicates += 1
            continue
        
        skus.append(sku)
        
        if period:
            if "sku_periods" not in config:
                config["sku_periods"] = {}
            config["sku_periods"][sku] = period
        
        added += 1
    
    if added > 0:
        config['skus'] = skus
        save_config(config)
    
    print(f"✅ Added: {added} new SKU(s)")
    if duplicates:
        print(f"ℹ️  Skipped: {duplicates} duplicate(s)")
    print(f"📋 Total: {len(skus)} SKU(s)")
    return 0

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    
    command = sys.argv[1]
    
    config = load_config()
    
    if command == 'list':
        return cmd_list(config)
    elif command == 'add':
        sku = None
        period = None
        args = sys.argv[2:]
        for i, arg in enumerate(args):
            if arg == '--period' and i + 1 < len(args):
                period = args[i + 1]
            elif not arg.startswith('--'):
                sku = arg
        return cmd_add(config, sku, period)
    elif command == 'remove':
        sku = sys.argv[2] if len(sys.argv) > 2 else None
        return cmd_remove(config, sku)
    elif command == 'period':
        sku = sys.argv[2] if len(sys.argv) > 2 else None
        period = sys.argv[3] if len(sys.argv) > 3 else None
        return cmd_period(config, sku, period)
    elif command == 'add-bulk':
        return cmd_add_bulk(config)
    else:
        print(f"❌ Unknown command: {command}")
        print(__doc__)
        return 1

if __name__ == "__main__":
    sys.exit(main())

