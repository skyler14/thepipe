#!/usr/bin/perl

use strict;
use warnings;
use JSON;

package User;

sub new {
    my ($class, $name, $age) = @_;
    my $self = {
        name => $name,
        age => $age
    };
    bless $self, $class;
    return $self;
}

sub greet {
    my ($self) = @_;
    return "Hello, " . $self->{name};
}

package main;

sub main {
    my $user = User->new("Alice", 30);
    print $user->greet() . "\n";
}

main();
