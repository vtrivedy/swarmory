# In separate mode Harbor builds the verifier's image from tests/, and the verifier runs as root.
FROM python:3.12-slim
# Pin the image by digest when you freeze the task.
COPY verifier.py key.json test.sh /tests/
RUN chmod 700 /tests && chmod 600 /tests/* && chmod 700 /tests/test.sh
