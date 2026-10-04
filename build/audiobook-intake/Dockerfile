FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY intake.py .

# Runs as root on purpose: the incoming share is mounted uid 1000 / 0755 and the
# library share uid 568 / 0755 (forced by CIFS mount options), and the job must
# delete from one and write to the other. The CronJob drops every capability
# except the two that need: DAC_OVERRIDE and FOWNER.
CMD ["python", "intake.py"]
