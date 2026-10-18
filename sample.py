from falkordb import FalkorDB
 
db = FalkorDB(host="r-6jissuruar.instance-cbx6vbdiq.hc-7up0crkyn.ap-south-1.aws.f2e0a955bb84.cloud", port=53319, username="falkordb", password="yEOhn7yoJm6h")
g = db.select_graph("ops")
 
g.query("""
    CREATE (:Service {name: 'checkout-web'})
           -[:DEPENDS_ON {weight: 1}]->
           (:Service {name: 'payments-api'})
""")
 
rows = g.ro_query("""
    MATCH (a:Service)-[:DEPENDS_ON]->(b:Service)
    RETURN a.name AS upstream, b.name AS downstream
""").result_set
 
print(rows)
# [['checkout-web', 'payments-api']]``