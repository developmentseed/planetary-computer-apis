FROM mcr.microsoft.com/azure-functions/python:4-python3.8

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

COPY functions/tiler /home/site/tiler
RUN pip install -r  /home/site/tiler/requirements.txt
