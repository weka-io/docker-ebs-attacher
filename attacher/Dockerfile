FROM alpine:3.3

RUN apk update
RUN apk add --no-cache py-pip file e2fsprogs

RUN pip install awscli boto3 requests plumbum retrying
ENV VOLUME_ID NONE
ADD attacher.py /scripts/attacher.py
CMD /scripts/attacher.py
