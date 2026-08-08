from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Hello from my Synology NAS!\n")


server = HTTPServer(("0.0.0.0", 8000), Handler)

print("Server listening on port 8000")
server.serve_forever()
