# In separate mode Harbor builds the verifier's image from tests/, and the verifier runs as root.
FROM python:3.12-slim
# Pin this base image by digest before you measure in Phase 5.
# Separate mode does not upload tests/. /tests holds only what this file copies, so COPY every file the verifier reads
# (key, snapshots, hidden cases), never the generator or cases/.
COPY verifier.py key.json test.sh /tests/
RUN chmod 700 /tests && chmod 600 /tests/* && chmod 700 /tests/test.sh
