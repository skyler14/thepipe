<?php
// Database connection
require_once 'config.php';

class UserManager {
    private $db;
    
    public function __construct($database) {
        $this->db = $database;
    }
    
    public function getAllUsers() {
        $stmt = $this->db->prepare("SELECT * FROM users ORDER BY created_at DESC");
        $stmt->execute();
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }
    
    public function addUser($name, $email) {
        $stmt = $this->db->prepare("INSERT INTO users (name, email) VALUES (?, ?)");
        return $stmt->execute([$name, $email]);
    }
    
    public function deleteUser($id) {
        $stmt = $this->db->prepare("DELETE FROM users WHERE id = ?");
        return $stmt->execute([$id]);
    }
}

$userManager = new UserManager($pdo);

// Handle AJAX requests
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    header('Content-Type: application/json');
    
    $action = $_POST['action'] ?? '';
    
    switch ($action) {
        case 'add':
            $result = $userManager->addUser($_POST['name'], $_POST['email']);
            echo json_encode(['success' => $result]);
            exit;
        
        case 'delete':
            $result = $userManager->deleteUser($_POST['id']);
            echo json_encode(['success' => $result]);
            exit;
    }
}

$users = $userManager->getAllUsers();
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>PHP User Manager</title>
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; }
        .user { border: 1px solid #ddd; padding: 10px; margin: 5px 0; }
        .error { color: red; }
        .success { color: green; }
    </style>
</head>
<body>
    <h1>User Management System</h1>
    
    <div id="message"></div>
    
    <form id="add-user-form">
        <input type="text" id="name" placeholder="Name" required>
        <input type="email" id="email" placeholder="Email" required>
        <button type="submit">Add User</button>
    </form>
    
    <div id="user-list">
        <?php foreach ($users as $user): ?>
            <div class="user" data-id="<?= $user['id'] ?>">
                <strong><?= htmlspecialchars($user['name']) ?></strong>
                - <?= htmlspecialchars($user['email']) ?>
                <button class="delete-btn" data-id="<?= $user['id'] ?>">Delete</button>
            </div>
        <?php endforeach; ?>
    </div>

    <script>
        $(document).ready(function() {
            // Add user handler
            $('#add-user-form').on('submit', function(e) {
                e.preventDefault();
                
                const name = $('#name').val();
                const email = $('#email').val();
                
                $.ajax({
                    url: '',
                    method: 'POST',
                    data: {
                        action: 'add',
                        name: name,
                        email: email
                    },
                    dataType: 'json',
                    success: function(response) {
                        if (response.success) {
                            $('#message').html('<p class="success">User added!</p>');
                            location.reload();
                        } else {
                            $('#message').html('<p class="error">Failed to add user</p>');
                        }
                    }
                });
            });
            
            // Delete user handler
            $('.delete-btn').on('click', function() {
                const userId = $(this).data('id');
                
                if (!confirm('Delete this user?')) return;
                
                $.ajax({
                    url: '',
                    method: 'POST',
                    data: {
                        action: 'delete',
                        id: userId
                    },
                    dataType: 'json',
                    success: function(response) {
                        if (response.success) {
                            $(`[data-id="${userId}"]`).fadeOut();
                        }
                    }
                });
            });
        });
    </script>
</body>
</html>
