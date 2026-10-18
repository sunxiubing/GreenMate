"""B05 Step 2 - Tool Definition (Function Call Schema)

供大模型自动调用四个底层业务函数。
每个工具包含: name / description / parameters / required
"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_environment",
            "description": (
                "Get the current greenhouse sensor readings (temperature, humidity, light). "
                "Use when: user asks about 'now' or 'current' environment conditions, "
                "or you need the latest reading as a decision basis. "
                "No parameters required."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_environment_history",
            "description": (
                "Get a time-window sequence of historical greenhouse environment data "
                "(temperature, humidity, light, scenario label). "
                "Use when: user asks about environmental change trends over past hours/days, "
                "statistical summary, anomaly detection, or needs time-series for trend analysis. "
                "Data is returned in chronological order (oldest first)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": (
                            "Length of the query time window in hours. "
                            "Example: hours=24 returns last 24 hours; hours=12 returns last 12 hours. "
                            "Range: 1-168 (7 days max)."
                        ),
                        "minimum": 1,
                        "maximum": 168,
                    },
                },
                "required": ["hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_forecast",
            "description": (
                "Get real-time weather forecast for a specified location "
                "(temperature, humidity, precipitation probability, cloud cover, solar radiation). "
                "Use when: user asks about outdoor weather conditions, "
                "impact of external weather changes on the greenhouse, "
                "or needs real meteorological data for comprehensive judgment. "
                "Note: status='unavailable' means the weather API is temporarily unreachable (e.g. network down) - "
                "prompt user to retry later; status='error' means invalid parameters (e.g. unknown location). "
                "Available locations: Beijing Daxing, Beijing Shunyi, Hebei Gu'an, Shandong Shouguang, "
                "Shanghai Chongming, Zhejiang Jiashan, Guangdong Guangzhou, Hainan Sanya, Jiangsu Nanjing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "Location name. Must be one of the 9 pre-configured greenhouse locations.",
                        "enum": [
                            "Beijing Daxing", "Beijing Shunyi", "Hebei Gu'an", "Shandong Shouguang",
                            "Shanghai Chongming", "Zhejiang Jiashan", "Guangdong Guangzhou",
                            "Hainan Sanya", "Jiangsu Nanjing",
                        ],
                    },
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_tomato_knowledge",
            "description": (
                "Search the tomato planting knowledge base and return the most relevant knowledge "
                "fragments with citations. "
                "Use when: user asks about system design, data sources, configuration parameters, "
                "disturbance scenarios, or technical details of GreenMate "
                "(e.g. 'SIMULATED vs PUBLIC difference', 'drought scenario humidity_delta', "
                "'why HTTP 400 does not return cached data'). "
                "The knowledge base only contains GreenMate internal documents and source code - "
                "it does NOT contain general agriculture knowledge. "
                "When all returned citation scores are 0, the knowledge base has no relevant content; "
                "do not fabricate an answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query. Use descriptive phrases rather than single words for better recall. "
                            "Examples: 'drought scenario humidity_delta', "
                            "'SIMULATED PUBLIC data source difference'."
                        ),
                    },
                    "top_k": {
                        "type": "number",
                        "description": "Number of most relevant results to return. Default: 5. Range: 1-20.",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
]


if __name__ == "__main__":
    import json
    print(json.dumps(TOOLS, indent=2, ensure_ascii=False))
