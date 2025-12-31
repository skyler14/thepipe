<?php

require_once 'vendor/autoload.php';

class User {
    private $name;
    private $email;
    
    public function __construct($name, $email) {
        $this->name = $name;
        $this->email = $email;
    }
    
    public function greet() {
        return "Hello, " . $this->name;
    }
}

function main() {
    $user = new User("Alice", "alice@example.com");
    echo $user->greet();
}

main();
?>
