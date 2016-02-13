FROM wekaio/alpine-python2:latest

RUN pip install boto3 requests plumbum retrying bunch
RUN apk add --no-cache file e2fsprogs

ENV VOLUME_ID NONE
ENV RESTART_SERVICE ""
ADD attacher.py /scripts/attacher.py
CMD /scripts/attacher.py
