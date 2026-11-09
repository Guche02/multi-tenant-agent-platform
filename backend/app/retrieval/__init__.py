# LlamaIndex's PGVectorStore names its table "data_" + the name it is given. The table is
# created and managed by LlamaIndex, not by our migrations (see migrations/env.py).
KB_TABLE_NAME = "knowledge_base"
KB_DB_TABLE = f"data_{KB_TABLE_NAME}"
