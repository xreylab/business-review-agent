from app.graph.builder import build_graph
graph = build_graph()
print("构图成功")
print(graph.get_graph().draw_mermaid())