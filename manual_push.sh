#!/bin/sh

docker build -t weka-ebs-attacher ./
docker tag weka-ebs-attacher:latest wekaio/ebs-attacher:latest
docker push wekaio/ebs-attacher
