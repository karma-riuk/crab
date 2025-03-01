FROM gradle:7.5-jdk11

# Install dependencies
RUN apt-get update && \
    apt-get install -y curl zip git && \
    rm -rf /var/lib/apt/lists/*

# Install SDKMAN!
RUN curl -s "https://get.sdkman.io" | bash

# Install multiple Java versions
RUN bash -c "source $HOME/.sdkman/bin/sdkman-init.sh && \
    sdk install java 8.0.302-open && \
    sdk install java 11.0.12-open && \
    sdk install java 17.0.12-oracle && \
    sdk install java 21.0.2-open && \
    sdk install java 23-open && \
    sdk default java 23-open"

WORKDIR /repo
