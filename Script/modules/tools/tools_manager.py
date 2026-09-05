import os
import json
from typing import Dict, List, Any

# ========== 文件路径：始终在脚本所在目录 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, "tools_data.json")   # 内部存储文件
EXPORT_FILE = os.path.join(SCRIPT_DIR, "tools.json")      # 最终导出的标准格式

# ========== 类型映射 ==========
TYPE_MAP = {
    "1": "string",
    "2": "integer",
    "3": "number",
    "4": "boolean",
    "5": "array",
    "6": "object"
}

# ========== 内部存储结构（方便编辑） ==========
# 格式：{ "tool_name": {"description": "...", "parameters": [...]} }
# 其中 parameters 为列表，每个元素：{"name":..., "type":..., "description":..., "required": bool}

def load_internal() -> Dict[str, Any]:
    """加载内部存储（简单 JSON 文件）"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_internal(data: Dict[str, Any]):
    """保存内部存储"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def export_standard(internal: Dict[str, Any]) -> Dict[str, List[Dict]]:
    """
    将内部存储转换为 OpenAI 标准格式：
    { "tools": [ { "type": "function", "function": {...} }, ... ] }
    """
    tools_list = []
    for name, info in internal.items():
        properties = {}
        required_list = []
        for p in info.get("parameters", []):
            properties[p["name"]] = {
                "type": p["type"],
                "description": p["description"]
            }
            if p.get("required", False):
                required_list.append(p["name"])
        tool_obj = {
            "type": "function",
            "function": {
                "name": name,
                "description": info["description"],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required_list,
                    "additionalProperties": False
                }
            }
        }
        tools_list.append(tool_obj)
    return {"tools": tools_list}

def save_standard(internal: Dict[str, Any]):
    """将标准格式写入 tools.json"""
    standard = export_standard(internal)
    with open(EXPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(standard, f, ensure_ascii=False, indent=2)
    print(f"✅ 已生成标准文件：{EXPORT_FILE}")

def display_tools(internal: Dict[str, Any]):
    if not internal:
        print("📭 当前没有任何工具。")
        return
    print("\n📋 现有工具列表：")
    for idx, name in enumerate(internal.keys(), 1):
        print(f"  {idx}. {name}")

def manage_parameters(params: list) -> list:
    """参数管理子菜单：增删改查，返回修改后的参数列表"""
    while True:
        print("\n--- 参数管理 ---")
        if params:
            print("当前参数列表：")
            for i, p in enumerate(params, 1):
                req_str = "必填" if p.get("required") else "可选"
                print(f"  {i}. {p['name']} ({p['type']}) [{req_str}]: {p['description']}")
        else:
            print("当前没有任何参数。")

        print("\n操作：")
        print("  a - 添加新参数")
        print("  e - 编辑参数")
        print("  d - 删除参数")
        print("  q - 完成并返回")
        choice = input("请选择操作 (a/e/d/q): ").strip().lower()

        if choice == 'a':
            # 添加新参数
            pname = input("参数名称: ").strip()
            if not pname:
                print("参数名称不能为空")
                continue
            if any(p['name'] == pname for p in params):
                print(f"参数 {pname} 已存在，请先编辑或使用其他名称")
                continue
            print("类型：1.string 2.integer 3.number 4.boolean 5.array 6.object")
            ptype_choice = input("请选择类型数字 (默认1): ").strip() or "1"
            ptype = TYPE_MAP.get(ptype_choice, "string")
            pdesc = input(f"参数 {pname} 的描述: ").strip()
            if not pdesc:
                pdesc = f"参数 {pname}"
            required = input("是否必填？(y/n, 默认 y): ").strip().lower()
            is_required = required != 'n'
            params.append({
                "name": pname,
                "type": ptype,
                "description": pdesc,
                "required": is_required
            })
            print(f"✅ 已添加参数 {pname}")

        elif choice == 'e':
            if not params:
                print("没有参数可编辑，请先添加。")
                continue
            try:
                idx = int(input("请输入要编辑的参数序号: ")) - 1
                if idx < 0 or idx >= len(params):
                    print("序号无效")
                    continue
                p = params[idx]
                print(f"正在编辑参数 [{p['name']}] (直接回车保留原值)：")
                new_name = input(f"  名称 ({p['name']}): ").strip()
                if new_name:
                    # 检查新名称是否与其他参数冲突（排除自身）
                    if any(p2['name'] == new_name for i2, p2 in enumerate(params) if i2 != idx):
                        print(f"参数名 {new_name} 已存在，修改取消")
                        continue
                    p['name'] = new_name
                print("  类型：1.string 2.integer 3.number 4.boolean 5.array 6.object")
                new_type_choice = input(f"  类型 (当前: {p['type']}): ").strip()
                if new_type_choice:
                    p['type'] = TYPE_MAP.get(new_type_choice, p['type'])
                new_desc = input(f"  描述 (当前: {p['description']}): ").strip()
                if new_desc:
                    p['description'] = new_desc
                new_req = input(f"  是否必填? (y/n, 当前: {'必填' if p['required'] else '可选'}): ").strip().lower()
                if new_req:
                    p['required'] = (new_req == 'y')
                print(f"✅ 参数已更新")
            except ValueError:
                print("请输入数字")

        elif choice == 'd':
            if not params:
                print("没有参数可删除。")
                continue
            try:
                idx = int(input("请输入要删除的参数序号: ")) - 1
                if 0 <= idx < len(params):
                    removed = params.pop(idx)
                    print(f"🗑️ 已删除参数 {removed['name']}")
                else:
                    print("序号无效")
            except ValueError:
                print("请输入数字")

        elif choice == 'q':
            break
        else:
            print("无效选择，请输入 a/e/d/q")
    return params

def add_or_edit_tool(internal: Dict[str, Any], tool_name: str = ""):
    if not tool_name:
        tool_name = input("✏️ 工具名称 (英文，唯一标识): ").strip()
        if not tool_name:
            print("❌ 工具名称不能为空")
            return

    existing = internal.get(tool_name, {})
    # 编辑描述
    default_desc = existing.get("description", "")
    desc = input(f"📝 工具描述 [{default_desc}]: ").strip()
    if not desc and default_desc:
        desc = default_desc
    if not desc:
        print("❌ 描述不能为空")
        return

    # 管理参数（复用现有参数列表）
    params = existing.get("parameters", []).copy()  # 拷贝一份，避免直接修改原数据
    print("\n现在配置该工具的参数（支持增删改查）：")
    params = manage_parameters(params)

    # 保存
    internal[tool_name] = {
        "description": desc,
        "parameters": params
    }
    save_internal(internal)
    save_standard(internal)
    print(f"✅ 工具 '{tool_name}' 已保存，共 {len(params)} 个参数。")

def delete_tool(internal: Dict[str, Any]):
    if not internal:
        print("没有工具可删除。")
        return
    display_tools(internal)
    names = list(internal.keys())
    try:
        idx = int(input("请输入要删除的工具序号: ")) - 1
        if 0 <= idx < len(names):
            deleted = names[idx]
            del internal[deleted]
            save_internal(internal)
            save_standard(internal)
            print(f"🗑️ 已删除工具 '{deleted}'")
        else:
            print("序号无效")
    except ValueError:
        print("请输入数字")

def preview_standard(internal: Dict[str, Any]):
    if not internal:
        print("没有工具，无法预览。")
        return
    standard = export_standard(internal)
    print("\n📄 标准 OpenAI Tool Calls 格式 (tools.json 内容):")
    print(json.dumps(standard, ensure_ascii=False, indent=2))

def main():
    internal = load_internal()
    # 如果已有 tools.json 但内部文件缺失，可以尝试导入（可选功能）
    if not internal and os.path.exists(EXPORT_FILE):
        print("🔍 检测到已有的 tools.json，是否导入？(y/n)", end=" ")
        if input().strip().lower() == 'y':
            try:
                with open(EXPORT_FILE, "r", encoding="utf-8") as f:
                    standard = json.load(f)
                # 反向解析为标准格式 -> 内部格式
                for tool in standard.get("tools", []):
                    func = tool.get("function", {})
                    name = func.get("name")
                    if name:
                        params = []
                        props = func.get("parameters", {}).get("properties", {})
                        required_list = func.get("parameters", {}).get("required", [])
                        for pname, pinfo in props.items():
                            params.append({
                                "name": pname,
                                "type": pinfo.get("type", "string"),
                                "description": pinfo.get("description", ""),
                                "required": pname in required_list
                            })
                        internal[name] = {
                            "description": func.get("description", ""),
                            "parameters": params
                        }
                save_internal(internal)
                print("✅ 导入成功")
            except Exception as e:
                print(f"❌ 导入失败: {e}")

    while True:
        print("\n" + "="*50)
        print("🔧 Tool Manager - 按工具名快速管理")
        print("1. 列出所有工具")
        print("2. 添加新工具")
        print("3. 编辑现有工具")
        print("4. 删除工具")
        print("5. 预览标准 JSON")
        print("6. 保存并退出")
        choice = input("请选择操作 (1-6): ").strip()

        if choice == "1":
            display_tools(internal)
        elif choice == "2":
            add_or_edit_tool(internal)
        elif choice == "3":
            if not internal:
                print("没有工具可编辑，请先添加。")
                continue
            display_tools(internal)
            names = list(internal.keys())
            try:
                idx = int(input("请选择要编辑的工具序号: ")) - 1
                if 0 <= idx < len(names):
                    add_or_edit_tool(internal, names[idx])
                else:
                    print("序号无效")
            except ValueError:
                print("请输入数字")
        elif choice == "4":
            delete_tool(internal)
        elif choice == "5":
            preview_standard(internal)
        elif choice == "6":
            save_internal(internal)
            save_standard(internal)
            print("👋 退出")
            break
        else:
            print("无效选择，请输入 1-6")

if __name__ == "__main__":
    main()