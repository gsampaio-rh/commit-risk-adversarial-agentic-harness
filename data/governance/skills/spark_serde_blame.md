---
id: spark_serde_blame
scope: project
project: SPARK
triggers: [serialization, serde, kryo, avro]
source: manual
trace_ref: ""
---

# Spark Serialization Bugs

When JIRA mentions serialization, Kryo, Avro, or SerDe errors, prioritize
examining commits that modify `*SerDe*.java` or `*Serializer*.java` in the
candidate set before unrelated candidates.
