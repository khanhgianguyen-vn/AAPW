"# AAPW" 

pm2 delete aapw
pm2 start C:\Users\TPTL\Documents\GitHub\AAPW\server.py --name aapw --interpreter C:\Users\TPTL\Documents\GitHub\AAPW\venv\Scripts\python.exe
pm2 save
pm2 logs aapw