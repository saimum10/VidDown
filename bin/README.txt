এখানে প্যাকেজিংয়ের সময় এই static binary গুলো রাখবেন (amd64):

  ffmpeg
  ffprobe
  aria2c

এই তিনটা ফাইল এখানে থাকলে core/downloader.py-এর find_binary()
ফাংশন এগুলো সিস্টেম থেকে না খুঁজে এখান থেকেই ব্যবহার করবে —
তাহলে ইউজারের মেশিনে আলাদা করে এসব ইন্সটল করা লাগবে না।

মনে রাখবেন: প্রতিটা ফাইলে executable permission দিতে হবে —
    chmod +x bin/ffmpeg bin/ffprobe bin/aria2c
