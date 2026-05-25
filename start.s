#!/bin/sh 
#
#
mv err.log log/err_$(date -d "today" +"%Y%m%d%H%M").log
mv out.log log/out_$(date -d "today" +"%Y%m%d%H%M").log
#
python main.py -cfg config/serverCwva.rson > out.log  2> err.log &

