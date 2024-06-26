FROM ubuntu:20.04

RUN apt-get update --fix-missing
RUN DEBIAN_FRONTEND=noninteractive apt-get install -y wget unzip curl gnupg \
    apt-transport-https \
    python3-pip \
    jq \
    git \
    libicu66

# Install Azure Function Tools

RUN curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /etc/apt/trusted.gpg.d/microsoft.gpg
RUN echo "deb [arch=amd64] https://packages.microsoft.com/repos/microsoft-ubuntu-focal-prod focal main" \
    > /etc/apt/sources.list.d/dotnetdev.list

RUN apt-get update && apt-get install -y azure-functions-core-tools-4

# Install Terraform 1.8.2

RUN wget -O terraform.zip https://releases.hashicorp.com/terraform/1.8.2/terraform_1.8.2_linux_amd64.zip
RUN unzip terraform.zip
RUN mv terraform /usr/local/bin

# Install kubectl

RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
RUN install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl


# Install Helm

RUN curl https://baltocdn.com/helm/signing.asc |  apt-key add -
RUN echo "deb https://baltocdn.com/helm/stable/debian/ all main" | tee /etc/apt/sources.list.d/helm-stable-debian.list
RUN apt-get update
RUN apt-get install helm=3.14.0-1

# Install kubelogin

RUN curl -sL https://github.com/Azure/kubelogin/releases/download/v0.0.18/kubelogin-linux-amd64.zip --output kubelogin.zip \
    && unzip -j kubelogin.zip bin/linux_amd64/kubelogin -d /usr/local/bin/ \
    && rm -rf kubelogin.zip

# Install azure client
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash

# Install Jinja
RUN pip3 install Jinja2 pyyaml==6.0


WORKDIR /opt/src
